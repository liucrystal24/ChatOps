"""独立虚构业务语料真实评测；标签只用于离线评分，不参与回答。"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import sys
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from evaluate_stage5 import cleanup, load_environment  # noqa: E402
from stage5_metrics import score_case, summarize_evidence, validate_cases  # noqa: E402


def load_suite(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    assert (
        manifest["cases_sha256"]
        == hashlib.sha256((folder / "cases.jsonl").read_bytes()).hexdigest()
    )
    corpus = {}
    for source in manifest["sources"]:
        path = folder / "corpus" / source["file"]
        assert path.parent == folder / "corpus"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        corpus[path.name] = path.read_text()
    cases = [json.loads(line) for line in (folder / "cases.jsonl").read_text().splitlines()]
    validate_cases(cases, corpus)
    assert len(cases) == manifest["case_count"]
    return manifest, cases, corpus


def catalog_sources(question, sources):
    from app.modules.chat.evidence import catalog

    known = {s["source_label"]: s for s in sources}
    result = []
    for unit in catalog(question, sources):
        source = known[unit["source_label"]]
        for a, b in unit["spans"]:
            result.append(
                {
                    **source,
                    "char_start": source["char_start"] + a,
                    "char_end": source["char_start"] + b,
                    "excerpt": source["excerpt"][a:b],
                }
            )
    return result


async def track_last_usage(parts, records):
    usage = None
    try:
        async for part in parts:
            usage = part.get("usage", usage)
            yield part
    finally:
        # 流可能多次携带累计usage；每个请求只计最后一次，不能按事件相加。
        if usage is not None:
            records.append(usage)


def saved_usage_summary(report):
    usages = []
    for row in report["rows"]:
        for usage in (
            row.get("usage"),
            (row.get("preliminary") or {}).get("usage"),
            (row.get("timings") or {}).get("rewrite_usage"),
        ):
            if usage is not None:
                usages.append(usage)
    return dict(
        requests_with_usage=len(usages),
        prompt_tokens=sum(u.get("prompt_tokens", 0) for u in usages),
        completion_tokens=sum(u.get("completion_tokens", 0) for u in usages),
    )


async def run(args):
    from fastapi import UploadFile
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.config import load_settings
    from app.core.pagination import Cursors
    from app.core.security import LoginLimiter, Security
    from app.infrastructure.chat_model import ChatModel
    from app.infrastructure.database import create_engine
    from app.infrastructure.embedding import Embedding
    from app.infrastructure.files import Files
    from app.infrastructure.models import IndexVersion
    from app.infrastructure.repositories import Database
    from app.infrastructure.vectors import Vectors
    from app.modules.chat.schemas import ChatInput, ConversationCreate
    from app.modules.chat.service import ChatService
    from app.modules.documents.schemas import Metadata
    from app.modules.documents.service import DocumentService
    from app.modules.identity.service import IdentityService
    from app.modules.indexing.service import IndexingService
    from app.modules.knowledge_bases.schemas import KnowledgeBaseCreate
    from app.modules.knowledge_bases.service import KnowledgeBaseService
    from app.modules.retrieval.schemas import RetrievalInput
    from app.modules.retrieval.service import RetrievalService

    settings = load_settings()
    if os.environ.get("ALLOW_TEST_DATABASE") != "1" or not make_url(
        settings.database_url.get_secret_value()
    ).database.endswith("_test"):
        raise SystemExit("仅允许显式 *_test 专用数据库")
    manifest, cases, corpus = load_suite(args.suite.resolve())
    engine = create_engine(settings)
    database = Database(async_sessionmaker(engine, expire_on_commit=False))
    security = Security(settings)
    cursors = Cursors(settings.auth_secret.get_secret_value())
    identity = IdentityService(database, security, LoginLimiter())
    knowledge = KnowledgeBaseService(database, cursors)
    files = Files(settings)
    documents = DocumentService(database, files, cursors, settings)
    embedding, vectors, model = Embedding(settings), Vectors(settings), ChatModel(settings)
    worker = IndexingService(database, files, embedding, vectors, settings)
    chat = ChatService(
        database, RetrievalService(database, embedding, vectors, settings), model, cursors, settings
    )
    counts = {"chat_http": 0, "embedding_http": 0, "usage": []}

    async def chat_request(request):
        counts["chat_http"] += 1

    async def embedding_request(request):
        counts["embedding_http"] += 1

    model.client.event_hooks["request"] = [chat_request]
    embedding.client.event_hooks["request"] = [embedding_request]
    original = model.stream

    async def tracked(*values, **options):
        async for part in track_last_usage(original(*values, **options), counts["usage"]):
            yield part

    model.stream = tracked
    output = (
        ROOT
        / "evaluation/outputs"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-business-v1")
    )
    output.mkdir(parents=True, exist_ok=False)
    paths = [
        "scripts/evaluate_business.py",
        "scripts/stage5_metrics.py",
        "backend/uv.lock",
        "backend/app/modules/chat/evidence.py",
        "backend/app/modules/chat/constraints.py",
        "backend/app/modules/documents/markdown_tables.py",
        "backend/app/modules/indexing/representation.py",
        "backend/app/modules/indexing/service.py",
        "backend/app/core/config.py",
        "backend/app/modules/chat/prompt.py",
        "backend/app/modules/chat/service.py",
        "backend/app/infrastructure/chat_model.py",
        "backend/app/modules/retrieval/service.py",
        "backend/app/modules/indexing/parser.py",
    ]
    report = dict(
        started_at=datetime.now(UTC).isoformat(),
        suite=manifest,
        suite_directory=str(args.suite.relative_to(ROOT)),
        python=platform.python_version(),
        model=model.snapshot(),
        index_config=worker.snapshot(),
        top_k=5,
        source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
        gold_used_in_generation=False,
        human_reviewed=False,
        rows=[],
        document_mappings={},
    )

    def save():
        report["model_calls"] = {k: v for k, v in counts.items() if k != "usage"}
        report["model_calls"]["requests_with_usage"] = len(counts["usage"])
        for field in ("prompt_tokens", "completion_tokens"):
            report["model_calls"][field] = sum(u.get(field, 0) for u in counts["usage"])
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    user_id = kb_id = None
    try:
        await model.ensure_ready()
        user = await identity.create_user("business_eval_" + uuid4().hex, "eval-" + uuid4().hex)
        user_id = str(user.id)
        kb = await knowledge.create(user_id, KnowledgeBaseCreate(name="原创虚构业务干扰评测v1"))
        kb_id = str(kb.id)
        for filename, text in corpus.items():
            uploaded = await documents.upload(
                user_id,
                kb_id,
                UploadFile(BytesIO(text.encode()), filename=filename),
                Metadata(tags=["synthetic-business-v1"]),
            )
            await worker.run_once()
            document = await documents.get(user_id, str(uploaded.document.id))
            if document.availability != "searchable":
                raise RuntimeError("测试语料索引未成功")
            report["document_mappings"][filename] = str(document.id)
            print("indexed", filename, flush=True)
        async with database.transaction() as repo:
            report["index_inventory"] = [
                dict(
                    document_id=d.id,
                    version_id=v.id,
                    chunk_count=v.chunk_count,
                    config=v.config_snapshot,
                )
                for d, v in await repo.active_versions(kb_id)
            ]
        save()
        for case in cases:
            conv = await chat.create(
                user_id, ConversationCreate(kb_id=kb_id, title=case["case_id"])
            )

            async def ask(question, conversation_id=str(conv.id), case_id=case["case_id"]):
                generation = await chat.prepare(
                    user_id,
                    conversation_id,
                    ChatInput(client_request_id=uuid4(), question=question),
                    "business-" + case_id,
                )
                events = [event async for event in chat.stream(generation)]
                stored = await chat.message(user_id, generation.message_id)
                return generation, events, stored

            seed = None
            if case.get("history_question"):
                _, _, preliminary = await ask(case["history_question"])
                seed = preliminary.model_dump(mode="json")
            generation, events, stored = await ask(case["question"])
            row = dict(
                case_id=case["case_id"],
                question=case["question"],
                category=case["category"],
                answerable=case["answerable"],
                reference_answer=case["reference_answer"],
                **stored.model_dump(mode="json"),
                sources=generation.sources,
                retrieval_query=generation.query,
                preliminary=seed,
                terminal_event=events[-1].event,
            )
            row["evidence_v2"] = score_case(case, generation.sources, report["document_mappings"])
            row["catalog_evidence"] = score_case(
                case,
                catalog_sources(generation.query, generation.sources),
                report["document_mappings"],
            )
            # 诊断扩大到20条只在回答后执行，不能将诊断候选送回生成或混入Top-5成绩。
            if case["answerable"]:
                candidates = await chat.retrieval.search(
                    user_id, kb_id, RetrievalInput(question=generation.query, top_k=20)
                )
                row["diagnostic_candidates"] = candidates
                row["diagnostic_top20"] = score_case(case, candidates, report["document_mappings"])
            row["automatic_checks"] = dict(
                expected_answer_type=(
                    stored.answer_type == ("answer" if case["answerable"] else "abstention")
                ),
                required_present=all(w in stored.content for w in case["required"]),
                forbidden_absent=not any(w in stored.content for w in case["forbidden"]),
            )
            report["rows"].append(row)
            save()
            print(
                case["case_id"], stored.status, stored.answer_type, row["evidence_v2"], flush=True
            )
        report["finished_at"] = datetime.now(UTC).isoformat()
        unsupported = [r for r in report["rows"] if not r["answerable"]]
        citations = [c for r in report["rows"] for c in r["citations"]]
        report["summary"] = dict(
            evidence=summarize_evidence(report["rows"]),
            completed=sum(r["status"] == "completed" for r in report["rows"]),
            failures=sum(r["status"] != "completed" for r in report["rows"]),
            unanswerable_abstentions=sum(r["answer_type"] == "abstention" for r in unsupported),
            unanswerable_total=len(unsupported),
            false_abstentions=sum(
                r["answer_type"] == "abstention" for r in report["rows"] if r["answerable"]
            ),
            valid_citations=sum(c["available"] for c in citations),
            citations_total=len(citations),
            human_correctness=None,
            human_fact_support=None,
        )
        save()
        print("report", str(output.relative_to(ROOT)), flush=True)
    finally:
        if kb_id:
            async with database.transaction() as repo:
                versions = (
                    await repo.session.scalars(
                        select(IndexVersion).where(IndexVersion.kb_id == kb_id)
                    )
                ).all()
            for version in versions:
                if version.collection_name:
                    await vectors.delete_version(version.collection_name, version.id)
        if user_id:
            await cleanup(database, settings, user_id, kb_id)
        await model.close()
        await embedding.close()
        await vectors.close()
        await engine.dispose()
        security.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite", type=lambda p: Path(p).resolve(), default=ROOT / "evaluation/business-v1"
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--key-env-file", type=Path, required=True)
    args = parser.parse_args()
    values = load_environment(args.env_file)
    values["SILICONFLOW_API_KEY"] = load_environment(args.key_env_file).get(
        "SILICONFLOW_API_KEY", ""
    )
    os.environ.update(values)
    os.environ["ALLOW_TEST_DATABASE"] = "1"
    os.environ["FILES_DIR"] = str(ROOT / "artifacts/business-evaluation-files")
    try:
        asyncio.run(run(args))
    except Exception as error:
        print(
            "业务评测失败：" + type(error).__name__ + "；详情请核对已保存的公开测试报告。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
