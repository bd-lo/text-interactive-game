"""内置配方 JSON 目录（第 13 章 §13.2、第 16 章 §16.1；JSON-only，无新增依赖）。

由 textgame.services.recipe_catalog.RecipeCatalog.load_builtin() 读取；
模板键用 item.<name>（ItemTemplateId，复用第 5 章 §5.7），真实实例 ID 由框架在
制造事件 result.produced_items 中生成（D-M4-1）。
"""
