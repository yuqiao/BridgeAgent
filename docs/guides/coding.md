# 修改与运行测试

在项目根目录：

```bash
uv run python -m bridge_agent.interfaces.agent --config examples/coding.yaml --workspace . --env-file .env --env-override
```

模型可以读取文件、加载 Skill、用 `preview_change(path, content)` 预览一个完整文本文件替换，再用返回的 `change_id` 调用 `apply_change`。终端显示工作区、目标与 diff；输入 `y` 或 `yes` 才允许写入，其余输入及 EOF 均拒绝。聊天与审批共用串行输入通道。

`run_command(command="test", request_id="唯一标识")` 请求运行 YAML 中已声明的 argv 模板。模型工具描述列出可选名称。终端显示实际 argv 与工作区，并另行确认。Skill 可以指导这个工作流，但不能授予权限。`tools.coding` 不含 Skill，`tools.coding-skills` 同时包含文件、Skill 和修改/命令工具；一次仅启用一个工具集。

非交互调用默认拒绝副作用。自动化需由用户在 YAML 显式设置精确的路径和命令名，例如：

```yaml
- name: approval.policy
  config:
    write_paths: [src/example.py]
    commands: [test]
```

没有通配符。授权的是该进程中这些目标的操作，不是仅授权某个 diff；要逐次核对请保留默认配置并使用终端。默认旧示例仍为只读。

文件变更只支持已有父目录中的单个 UTF-8 小文件（新旧内容各最多 16000 字符），遵守读取相同的工作区、ignore 和保护文件策略。批准后再次检查原内容；过期预览拒绝覆盖。临时文件写完后原子替换；保留现有权限。没有跨文件事务，也不提供针对恶意并发文件系统修改的操作系统隔离。

命令不用 shell 拼接；stdin 关闭，只继承 PATH/HOME/LANG/LC_ALL/TMPDIR/VIRTUAL_ENV，不继承模型密钥。stdout 与 stderr 合计保留最多 65536 原始字节，超出仍排空并标记 truncated；默认超时 60 秒，可配置至 600 秒。取消、超时及插件关闭会终止进程组并等待回收。命令会执行仓库代码，可自行读本机文件，不是沙箱。当前支持 macOS/Linux。

预览 ID、命令请求 ID 的去重仅在本次宿主生命周期内有效；完成的重复请求不重做，未完成命令拒绝使用原 ID 重试。重启后不自动重放失败会话；先核对文件、测试及外部效果，再开新会话。checkpoint 不会回滚文件或进程的效果。
