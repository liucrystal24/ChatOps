"""在显式 *_test 数据库运行冻结公开题集；只清理本次拥有的资源，报告默认不提交。"""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["PYTHONPATH"] = str(ROOT / "backend")


def load_environment(path):
    return {
        k: v.strip().strip("\"'")
        for k, v in (
            line.split("=", 1)
            for line in path.read_text().splitlines()
            if line and not line.startswith("#")
        )
    }


async def cleanup(database, settings, user_id, kb_id):
    from sqlalchemy import delete, select, update

    from app.infrastructure.models import (
        Chunk,
        Citation,
        Conversation,
        Document,
        IndexVersion,
        Job,
        KnowledgeBase,
        Membership,
        Message,
        User,
    )

    async with database.transaction() as repo:
        await repo.lock_authorization()
        convs = select(Conversation.id).where(Conversation.user_id == user_id)
        msgs = select(Message.id).where(Message.conversation_id.in_(convs))
        await repo.session.execute(delete(Citation).where(Citation.message_id.in_(msgs)))
        await repo.session.execute(
            update(Conversation)
            .where(Conversation.user_id == user_id)
            .values(active_message_id=None)
        )
        await repo.session.execute(
            delete(Message).where(Message.conversation_id.in_(convs), Message.role == "assistant")
        )
        await repo.session.execute(delete(Message).where(Message.conversation_id.in_(convs)))
        await repo.session.execute(delete(Conversation).where(Conversation.user_id == user_id))
        if kb_id:
            keys = (
                await repo.session.scalars(
                    select(Document.storage_key).where(Document.kb_id == kb_id)
                )
            ).all()
            await repo.session.execute(
                update(Document)
                .where(Document.kb_id == kb_id)
                .values(active_index_version_id=None, current_job_id=None)
            )
            await repo.session.execute(delete(Chunk).where(Chunk.kb_id == kb_id))
            await repo.session.execute(delete(IndexVersion).where(IndexVersion.kb_id == kb_id))
            await repo.session.execute(
                update(Job).where(Job.kb_id == kb_id).values(retry_of_job_id=None)
            )
            await repo.session.execute(delete(Job).where(Job.kb_id == kb_id))
            await repo.session.execute(delete(Document).where(Document.kb_id == kb_id))
            await repo.session.execute(delete(Membership).where(Membership.kb_id == kb_id))
            await repo.session.execute(
                delete(KnowledgeBase).where(
                    KnowledgeBase.id == kb_id, KnowledgeBase.created_by == user_id
                )
            )
            for key in keys:
                (settings.files_dir / key).unlink(missing_ok=True)
        await repo.session.execute(delete(User).where(User.id == user_id))


def summarize(rows):
    if rows and all(r.get("status") == "retrieval_only" for r in rows):
        return {"n": len(rows), "mode": "retrieval_only", "generation_executed": False}
    answered = [r for r in rows if r["answerable"]]
    unsupported = [r for r in rows if not r["answerable"]]
    abstained = [r for r in rows if r.get("answer_type") == "abstention"]
    citations = [c for r in rows for c in r.get("citations", [])]

    def quantile(values, p):
        if not values:
            return None
        values = sorted(values)
        return round(values[min(len(values) - 1, int((len(values) - 1) * p))], 2)

    latency = {}
    for field in ("retrieval_ms", "rewrite_ms", "evidence_check_ms", "first_token_ms", "total_ms"):
        values = [r["timings"][field] for r in rows if r.get("timings", {}).get(field) is not None]
        latency[field] = {
            "p50": quantile(values, 0.5),
            "p95": quantile(values, 0.95),
            "n": len(values),
        }
    hits = sum(r.get("evidence_hit", False) for r in answered)
    rejected = sum(r.get("answer_type") == "abstention" for r in unsupported)
    return dict(
        n=len(rows),
        completed=sum(r.get("status") == "completed" for r in rows),
        failures=sum(r.get("status") != "completed" for r in rows),
        evidence_recall_at_5={
            "hits": hits,
            "total": len(answered),
            "value": hits / len(answered) if answered else None,
        },
        citations={
            "valid": sum(c["available"] for c in citations),
            "total": len(citations),
            "validity": sum(c["available"] for c in citations) / len(citations)
            if citations
            else None,
        },
        abstention={
            "true": rejected,
            "unanswerable": len(unsupported),
            "recall": rejected / len(unsupported) if unsupported else None,
            "false_abstentions": sum(r.get("answer_type") == "abstention" for r in answered),
            "precision": rejected / len(abstained) if abstained else None,
        },
        reference_fact_presence_proxy=sum(r.get("reference_fact_presence", False) for r in answered)
        / len(answered)
        if answered
        else None,
        human_correctness=None,
        human_fact_support=None,
        latency=latency,
        answer_usage={
            "prompt_tokens": sum((r.get("usage") or {}).get("prompt_tokens", 0) for r in rows),
            "completion_tokens": sum(
                (r.get("usage") or {}).get("completion_tokens", 0) for r in rows
            ),
            "requests_with_usage": sum(bool(r.get("usage")) for r in rows),
        },
    )


async def run(args):
    from fastapi import UploadFile
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.config import load_settings
    from app.core.pagination import Cursors
    from app.core.security import LoginLimiter, Security
    from app.infrastructure.chat_model import ChatModel
    from app.infrastructure.database import create_engine
    from app.infrastructure.embedding import Embedding
    from app.infrastructure.files import Files
    from app.infrastructure.repositories import Database
    from app.infrastructure.vectors import Vectors
    from app.modules.chat.prompt import SYSTEM
    from app.modules.chat.schemas import ChatInput, ConversationCreate
    from app.modules.chat.service import ChatService
    from app.modules.documents.schemas import Metadata
    from app.modules.documents.service import DocumentService
    from app.modules.identity.service import IdentityService
    from app.modules.indexing.service import IndexingService
    from app.modules.knowledge_bases.schemas import KnowledgeBaseCreate
    from app.modules.knowledge_bases.service import KnowledgeBaseService
    from app.modules.retrieval.service import RetrievalService

    settings = load_settings()
    if os.environ.get("ALLOW_TEST_DATABASE") != "1" or not make_url(
        settings.database_url.get_secret_value()
    ).database.endswith("_test"):
        raise SystemExit("只允许 ALLOW_TEST_DATABASE=1 的 *_test 专用数据库")
    cases_path = args.cases.resolve()
    from stage5_metrics import score_case, summarize_evidence, validate_cases

    v2_cases = [
        json.loads(line) for line in (ROOT / "evaluation/cases-v2.jsonl").read_text().splitlines()
    ]
    v2_by_id = {c["case_id"]: c for c in v2_cases}
    corpus_text = {p.name: p.read_text() for p in (ROOT / "evaluation/corpus/v1").glob("*.md")}
    validate_cases(v2_cases, corpus_text)
    frozen = json.loads((ROOT / "evaluation/manifest-v2.json").read_text())
    assert (
        frozen["cases_sha256"]
        == hashlib.sha256((ROOT / "evaluation/cases-v2.jsonl").read_bytes()).hexdigest()
    )
    cases = [json.loads(line) for line in cases_path.read_text().splitlines()]
    if args.split != "all":
        cases = [c for c in cases if c["split"] == args.split]
    engine = create_engine(settings)
    database = Database(async_sessionmaker(engine, expire_on_commit=False))
    embedding, vectors, model = (
        Embedding(settings),
        Vectors(settings),
        ChatModel(settings),
    )
    security = Security(settings)
    identity = IdentityService(database, security, LoginLimiter())
    cursors = Cursors(settings.auth_secret.get_secret_value())
    knowledge = KnowledgeBaseService(database, cursors)
    documents = DocumentService(database, Files(settings), cursors, settings)
    worker = IndexingService(database, Files(settings), embedding, vectors, settings)
    chat = ChatService(
        database,
        RetrievalService(database, embedding, vectors, settings),
        model,
        cursors,
        settings,
    )
    calls = {"http_attempts": 0, "embedding_http_attempts": 0, "returned_usage": []}

    async def record_request(request):
        if request.url.path.endswith("/chat/completions"):
            calls["http_attempts"] += 1

    async def record_embedding(request):
        calls["embedding_http_attempts"] += 1

    model.client.event_hooks["request"] = [record_request]
    embedding.client.event_hooks["request"] = [record_embedding]
    original_stream = model.stream

    async def tracked_stream(*values, **options):
        usage = None
        try:
            async for part in original_stream(*values, **options):
                usage = part.get("usage", usage)
                yield part
        finally:
            if usage is not None:
                calls["returned_usage"].append(usage)

    model.stream = tracked_stream
    user_id = kb_id = profile = None
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "split": args.split,
        "python": platform.python_version(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in (
                "fastapi",
                "pydantic",
                "SQLAlchemy",
                "httpx",
                "tokenizers",
                "pypdf",
                "langchain-text-splitters",
            )
        },
        "backend_lock_sha256": hashlib.sha256((ROOT / "backend/uv.lock").read_bytes()).hexdigest(),
        "prompt_module_sha256": hashlib.sha256(
            (ROOT / "backend/app/modules/chat/prompt.py").read_bytes()
        ).hexdigest(),
        "chat_service_sha256": hashlib.sha256(
            (ROOT / "backend/app/modules/chat/service.py").read_bytes()
        ).hexdigest(),
        "chat_adapter_sha256": hashlib.sha256(
            (ROOT / "backend/app/infrastructure/chat_model.py").read_bytes()
        ).hexdigest(),
        "git_revision": (
            await asyncio.to_thread(
                subprocess.run,
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        ).stdout.strip()
        or None,
        "model": model.snapshot(),
        "embedding_model": settings.embedding_model,
        "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "evidence_v2_sha256": hashlib.sha256(
            (ROOT / "evaluation/cases-v2.jsonl").read_bytes()
        ).hexdigest(),
        "retrieval_only": args.retrieval_only,
        "index_config": worker.snapshot(),
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                ROOT / "scripts/stage5_metrics.py",
                ROOT / "backend/app/modules/chat/evidence.py",
                ROOT / "backend/app/modules/chat/constraints.py",
                ROOT / "backend/app/modules/documents/markdown_tables.py",
                ROOT / "backend/app/modules/indexing/representation.py",
                ROOT / "backend/app/core/config.py",
                ROOT / "scripts/evaluate_stage5.py",
                ROOT / "backend/app/modules/retrieval/service.py",
                ROOT / "backend/app/modules/indexing/parser.py",
                ROOT / "backend/app/modules/indexing/service.py",
            ]
        },
        "corpus": json.loads((ROOT / "evaluation/manifest-v1.json").read_text()),
        "top_k": 5,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "annotation_status": "agent-reviewed-human-pending",
        "review_method": (
            "自动证据定位和关键事实包含率；人工正确率/支持率尚未评审，不以代理分数冒充人工验收。"
        ),
        "rows": [],
    }
    output = (
        ROOT / "evaluation/outputs" / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{args.split}"
    )
    output.mkdir(parents=True, exist_ok=False)

    def save():
        report["model_calls"] = {
            "http_attempts": calls["http_attempts"],
            "embedding_http_attempts": calls["embedding_http_attempts"],
            "requests_with_usage": len(calls["returned_usage"]),
            "prompt_tokens": sum(u["prompt_tokens"] for u in calls["returned_usage"]),
            "completion_tokens": sum(u["completion_tokens"] for u in calls["returned_usage"]),
        }
        report["summary"] = summarize(report["rows"])
        report["evidence_v2_summary"] = summarize_evidence(report["rows"])
        report["evidence_v2_by_split"] = {
            split: summarize_evidence([r for r in report["rows"] if r["split"] == split])
            for split in {r["split"] for r in report["rows"]}
        }
        report["legacy_v1_summary"] = summarize(
            [r for r in report["rows"] if r["case_id"].startswith(("s", "f", "u"))]
        )
        report["holdout_summary"] = (
            summarize([r for r in report["rows"] if r["split"] == "holdout"])
            if any(r["split"] == "holdout" for r in report["rows"])
            else None
        )
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        (output / "review.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "case_id": r["case_id"],
                        "correct": None,
                        "supported": None,
                        "reviewer": None,
                        "notes": "",
                        "fact_reviews": [],
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for r in report["rows"]
            )
        )

    try:
        user = await identity.create_user("eval_" + uuid4().hex, "eval-" + uuid4().hex)
        user_id = str(user.id)
        kb = await knowledge.create(user_id, KnowledgeBaseCreate(name="项目公开文档评测 v1"))
        kb_id = str(kb.id)
        mappings = {}
        for source in report["corpus"]["sources"]:
            path = ROOT / "evaluation/corpus/v1" / source["file"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
            uploaded = await documents.upload(
                user_id,
                kb_id,
                UploadFile(BytesIO(path.read_bytes()), filename=path.name),
                Metadata(tags=["public-evaluation"]),
            )
            await worker.run_once()
            document = await documents.get(user_id, str(uploaded.document.id))
            print(
                "index",
                path.name,
                document.availability,
                document.latest_job.status,
                document.latest_job.error.code if document.latest_job.error else "",
                flush=True,
            )
            assert document.availability == "searchable", "公开评测语料索引失败"
            mappings[source["file"]] = str(document.id)
        report["document_mappings"] = mappings
        async with database.transaction() as repo:
            kb = await repo.knowledge_base(kb_id)
            profile = kb.embedding_profile
            report["embedding_profile"] = profile
            report["index_versions"] = [v.id for _, v in await repo.active_versions(kb_id)]
            report["index_inventory"] = [
                dict(
                    document_id=d.id,
                    index_version_id=v.id,
                    chunk_count=v.chunk_count,
                    config=v.config_snapshot,
                )
                for d, v in await repo.active_versions(kb_id)
            ]
        print("公开语料真实索引完成；开始固定题集", flush=True)
        for case in cases:
            if args.retrieval_only:
                from app.modules.retrieval.schemas import RetrievalInput

                query = case["question"]
                rewrite_usage = None
                if case.get("history_question"):
                    query, rewrite_usage = await model.rewrite(
                        [{"role": "user", "content": case["history_question"]}], query
                    )
                candidates = await chat.retrieval.search(
                    user_id, kb_id, RetrievalInput(question=query, top_k=20)
                )
                row = dict(
                    case_id=case["case_id"],
                    split=case["split"],
                    question=case["question"],
                    answerable=case["answerable"],
                    status="retrieval_only",
                    answer_type=None,
                    citations=[],
                    retrieval_query=query,
                    sources=candidates[:5],
                    candidates=candidates,
                    rewrite_usage=rewrite_usage,
                )
                row["evidence_v2"] = score_case(v2_by_id[case["case_id"]], candidates[:5], mappings)
                row["candidate_evidence"] = {
                    str(k): score_case(v2_by_id[case["case_id"]], candidates[:k], mappings)
                    for k in (5, 10, 20)
                }
                report["rows"].append(row)
                save()
                print(case["case_id"], row["evidence_v2"], flush=True)
                continue
            conversation = await chat.create(
                user_id, ConversationCreate(kb_id=kb_id, title=case["case_id"])
            )

            async def ask(question, conversation_id=str(conversation.id), case_id=case["case_id"]):
                generation = await chat.prepare(
                    user_id,
                    conversation_id,
                    ChatInput(client_request_id=uuid4(), question=question),
                    "evaluation-" + case_id,
                )
                stream = [event async for event in chat.stream(generation)]
                stored = await chat.message(user_id, generation.message_id)
                return generation, stream, stored

            preliminary = None
            if case.get("history_question"):
                _, _, seed = await ask(case["history_question"])
                preliminary = seed.model_dump(mode="json")
            generation, stream, stored = await ask(case["question"])
            row = stored.model_dump(mode="json")
            row.update(
                case_id=case["case_id"],
                split=case["split"],
                question=case["question"],
                reference_answer=case["reference_answer"],
                answerable=case["answerable"],
                preliminary=preliminary,
                retrieval_query=generation.query,
                sources=generation.sources,
                terminal_event=stream[-1].event,
            )
            row["evidence_v2"] = score_case(v2_by_id[case["case_id"]], generation.sources, mappings)
            if args.diagnose and case["answerable"]:
                from app.modules.retrieval.schemas import RetrievalInput

                candidates = await chat.retrieval.search(
                    user_id, kb_id, RetrievalInput(question=generation.query, top_k=20)
                )
                row["candidates"] = candidates
                row["candidate_evidence"] = {
                    str(k): score_case(v2_by_id[case["case_id"]], candidates[:k], mappings)
                    for k in (5, 10, 20)
                }
            if case["answerable"] and "evidence" in case:
                # 每题一个预先标注的证据单元；允许切分边界，标注片段至少 90% 在单块中出现。
                evidence = case["evidence"]

                def hit(source, evidence=evidence, document_id=mappings[case["file"]]):
                    if source["document_id"] != document_id:
                        return False
                    text = source["excerpt"]
                    return evidence in text or any(
                        evidence[i : i + max(12, int(len(evidence) * 0.9))] in text
                        for i in range(max(1, len(evidence) // 10 + 1))
                    )

                row["evidence_hit"] = any(hit(s) for s in generation.sources)
                row["reference_fact_presence"] = stored.answer_type == "answer" and all(
                    any(value.lower() in stored.content.lower() for value in fact.split("|"))
                    for fact in case["acceptable_facts"]
                )
            report["rows"].append(row)
            save()
            print(
                f"{case['case_id']} {stored.status} {stored.answer_type or stored.error_code}",
                flush=True,
            )
        report["finished_at"] = datetime.now(UTC).isoformat()
        save()
        summary = report["summary"]
        (output / "report.md").write_text(
            "# 阶段 5 真实 Dense 基线\n\n"
            + json.dumps(summary, ensure_ascii=False, indent=2)
            + "\n\n人工正确率与支持率尚未复核。逐题答案和候选见 report.json，"
            "人工复核表见 review.jsonl。\n"
        )
        print(
            json.dumps(
                {
                    "report": str(output / "report.json"),
                    "summary": summary,
                    "evidence_v2_summary": report["evidence_v2_summary"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        if kb_id and profile:
            for _, version in await _active(database, kb_id):
                await vectors.delete_version(profile["collection"], version.id)
        if user_id:
            await cleanup(database, settings, user_id, kb_id)
        await model.close()
        await embedding.close()
        await vectors.close()
        await engine.dispose()
        security.close()


async def _active(database, kb_id):
    async with database.transaction() as repo:
        return await repo.active_versions(kb_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--key-env-file", type=Path)
    parser.add_argument("--split", choices=["all", "dev", "holdout", "regression"], default="all")
    parser.add_argument("--cases", type=Path, default=ROOT / "evaluation/cases-v2.jsonl")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    values = load_environment(args.env_file)
    if args.key_env_file:
        values["SILICONFLOW_API_KEY"] = load_environment(args.key_env_file).get(
            "SILICONFLOW_API_KEY", ""
        )
    for name in (
        "CHUNK_SIZE",
        "CHUNK_OVERLAP",
        "CHUNK_STRUCTURE",
        "EMBEDDING_TITLE_CONTEXT",
        "EMBEDDING_TABLE_CONTEXT",
    ):
        if name in os.environ:
            values[name] = os.environ[name]
    os.environ.update(values)
    os.environ["ALLOW_TEST_DATABASE"] = "1"
    os.environ["FILES_DIR"] = str(ROOT / "artifacts/evaluation-files")
    try:
        asyncio.run(run(args))
    except Exception as error:
        # 禁止原始 SQL/连接串/供应商响应进入控制台。
        print(
            "评测失败：" + type(error).__name__ + "；请检查专用测试服务与已保存报告。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
