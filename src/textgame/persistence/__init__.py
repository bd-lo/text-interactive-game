"""持久化层 (M2-008 SQLite 事件表/快照；M3 Alembic baseline；M4 多存档复合主键)。

- ``tables``：SQLAlchemy 2.0 ORM 表（``events`` 主键 ``(game_id, seq)``，同库多存档）；
- ``sqlite_repository`` / ``memory``：``GameRepository`` 两种实现（CAS、快照重建、审计只增）；
- ``versioning``：current 行缺失时的提交版本重建规则（M3 D-M3-6）；
- ``legacy_upgrade``：M2/M3 旧库显式升级脚本（备份 → 补列/建表 → Alembic upgrade head）。

本包为 import-linter 分层契约占位, 实现见开发文档第 14 章。
"""
