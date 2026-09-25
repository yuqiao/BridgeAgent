# 测试

后续功能测试放在此目录，文件名使用 `test_*.py`。
通过 `uv run pytest` 运行，使用 `importlib` 模式导入已安装的项目包，
无需手动修改 `PYTHONPATH`。

初始化阶段没有业务代码，也没有占位测试；当前运行 pytest 会以退出码 5
报告未收集到测试。添加首个功能测试时，在 `.github/workflows/ci.yml`
加入 `uv run --locked pytest` 步骤。
