# 本机备份与恢复

备份覆盖 MySQL 事实、Qdrant 派生索引和原文件三个命名卷。为避免三者处于不同写入时刻，脚本停止当前项目正在运行的 Web/API/Worker/MySQL/Qdrant，冷归档后重新 `start` 原容器；端口映射保持原样。备份期间服务短暂不可用。目录权限 0700，归档 0600，记录 SHA-256；其中包含用户业务数据，应按私有数据保管。

```bash
python3 scripts/backup_restore.py backup --env-file .env \
  --directory artifacts/backups/local-20261015 \
  --helper-image chat-rag-backend:0.5.0
```

辅助容器只运行 Python 标准库、关闭网络、只读挂载源卷；镜像需事先构建。脚本不包含 `.env`，恢复时必须另外提供原来的 MySQL 账户密码、Qdrant API Key、JWT 签名配置。不要把备份或密钥加入 Git。冷备份只支持相同平台及锁定 MySQL/Qdrant 版本；跨版本升级按各服务官方迁移流程另行验证。冷备份期间应停止其他通过 CLI/Navicat 直接写库的操作。

自动恢复限定为**全新**的 `*-test` 项目、`*_test` 数据库；任一目标卷已存在就拒绝覆盖。测试配置应保留源 MySQL database 名和密钥，修改 Compose 项目名与 Web/开发端口，防止连接现有实例。

```bash
python3 scripts/backup_restore.py restore-empty --env-file .env.restore-test \
  --directory artifacts/backups/local-20261015 \
  --helper-image chat-rag-backend:0.5.0
```

随后使用同版本镜像和恢复配置启动 Compose，检查迁移、登录、文档/会话数量、原文件 SHA、Qdrant points 和真实检索。没有这些检查不能宣称备份可恢复。恢复生产实例、覆盖已有卷、回滚现有数据均不在此脚本自动执行范围内，需另行制定和授权。

Qdrant 可从原文件重建，但数据库不能丢失。若只丢向量，应使用应用的重建索引功能并等待发布；不要把仍指向缺失 points 的 MySQL 版本当成可用。备份验收的实际执行结果见阶段 5 交付记录。

本轮已在全新测试卷实际恢复并验证登录、6条历史消息、原文件SHA、有效points和真实带引用问答。解压保持原数字UID/GID，避免0600文件变为root所有而无法由API读取；只跳过固定的MySQL运行时socket链接（启动时重建），其他绝对链接及路径穿越仍拒绝。已有卷覆盖拒绝也已实测。
