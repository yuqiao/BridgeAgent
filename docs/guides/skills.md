# 使用 Skill

阶段 3.5 已提供 `skills.filesystem` 插件。Skill 是带元数据的 Markdown 指导内容，插件负责发现、加载与资料访问。新增 Skill 无需修改 Agent 循环。

在 BridgeAgent 仓库根目录运行：

```bash
uv run --locked python -m bridge_agent.interfaces.agent \
  --config examples/skills.yaml --workspace . \
  --env-file .env --env-override --show-tools \
  --prompt '使用 code-map Skill，解释这个项目的 CLI 入口及调用链。'
```

如果指定其他工作区，将配置中的 `skills.filesystem.config.roots` 改成该工作区内的 Skill 目录。路径相对工作区解析，必须显式配置，不扫描用户主目录。支持 1–16 个不重复的根目录。

Skill 目录包含如下文件：

```text
skills/my-skill/SKILL.md
skills/my-skill/references/checklist.md
```

SKILL.md 格式：

```markdown
---
name: my-skill
description: 用一句话说明何时使用这个 Skill。
---
先读取 references/checklist.md，再使用已有工具完成任务并引用文件位置。
```

首期 front matter 只接受 name 和 description。名称使用小写字母、数字及中间连字符，最长 64 字符；描述为非空字符串，最长 500 字符。重复名称、重复字段、YAML 标签导致的非字符串值、锚点或别名均拒绝。元数据头最多 4,096 字符。

`tools.skills` 提供只读仓库的三个工具，并添加：

| 工具 | 作用 |
| --- | --- |
| list_skills | 返回名称、描述、相对路径；不向模型返回正文 |
| load_skill(name) | 仅返回指定 Skill 的元数据及正文 |
| read_skill_resource(name, path, start_line, limit) | 读取所属 Skill 目录内的资料，可分页，返回实际行号 |

不要同时启用 `tools.workspace` 和 `tools.skills`，它们都提供同一工具集服务。工具集仍可被另一个插件替换。

发现时在本地读取文件来解析元数据；只有模型调用 load_skill 才把所选正文写入工具结果和当前 checkpoint 上下文。没有另一份 Skill 消息历史。Skill 内容不会自动安装插件、执行脚本或取得写入权限。

资料沿用 [文件访问策略](workspace.md)，且真实目标必须留在所属 Skill 目录；`..`、绝对路径、越界符号链接和敏感文件不可访问。读取方法新增可选 `scope` 约束，第三方文件 provider 应保留这一约束。

单次资料读取最多 200 行、16,000 文本字符；用 start_line 继续读取。SKILL.md 正文加载首期要求整个文档在一次读取窗口内，超出则拒绝，建议把长内容拆成参考资料。目录列表若被遍历预算或 200 条结果限制截断，发现会报错，不能把不完整目录当成完整 Skill 列表。

真实模型工具端点当前仍存在限流；本阶段提供确定性模型与实际 CLI 的闭环验证，不承诺模型一定遵循所有自然语言步骤。

```bash
uv run --locked pytest tests/test_skills.py tests/test_skill_agent.py tests/test_workspace_cli.py
```
