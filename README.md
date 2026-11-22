# release_budget — 离线隐私发布审计

回答审批台上的那个问题：**同一个人分属两个研究队列时，为什么两份各自合规的统计报告联发却超预算？**

每个发布自身都在预算内，但同一个自然人（以不同来源成员编号分别出现在两个队列里）在两个发布中都被收费，两份报告在同一个时间窗、同一个用途组中叠加，合并后的个人消耗就超过了上限。

本程序只做**审计**：读入试验数据里的有理数成本，按合同规则计算精确的隐私消耗，给出预算是否在**每一种可行身份解释**下合规，并在超额时给出可复核的证明。程序**没有任何修改数据或训练模型的机制**。所有参与者、队列与发布项均为虚构。

要求：Windows 原生 Python 3.12，只用标准库，纯 CPU。

---

## 快速开始

```bash
# 审计默认对照表下的全部历史发布
python -m release_budget audit cases/case.json

# 录入新对照表后，回看历史视图 V-ORIGINAL，并报告哪些结论发生变化
python -m release_budget recheck cases/case.json

# 在待发布候选中挑选满足预算的固定子集
python -m release_budget choose cases/choose.json

# 独立穷举与快速审计的一致性校验
python -m release_budget verify cases/case.json
```

全部命令输出**单个确定性 UTF-8 JSON 对象**到 stdout。输入被拒绝时输出错误对象并以退出码 `2` 结束；成功命令退出码为 `0`。

---

## 命令行接口

```
python -m release_budget <命令> CASE.json [选项]
```

| 命令 | 选项 | 作用 |
| --- | --- | --- |
| `audit` | `--view VIEW_ID` | 在指定历史审计视图（或默认对照表）下审计全部计入成本的历史发布 |
| `recheck` | `--view VIEW_ID` `--new-linkage LINKAGE_ID` | 先重放历史视图，再按新录入的对照表重算，报告哪些结论变化 |
| `choose` | `--view VIEW_ID` | 从待发布候选中挑一个固定子集（保留全部强制项、任一身份解释下不超预算） |
| `verify` | `--view VIEW_ID` | 用独立穷举交叉验证快速审计；两者不一致时列出差异单元 |

`--view` 缺省时：`audit`/`choose`/`verify` 使用 `active_linkage`；`recheck` 使用案例文件 `recheck` 段里的缺省配置。命令参数也可由案例文件提供，命令行优先。

错误对象形如：

```json
{"ok":false,"error":{"code":"invalid-rational","message":"exposure cost 'oops' is not a rational ...","path":"releases[0].cost","record":"R-ALPHA-1"}}
```

`path` 精确到字段下标，`record` 给出该字段所属记录的 ID，因此任何输入错误都能直接定位到具体字段与具体记录。

---

## 案例文件格式

案例是一份 JSON。`case_version` 必须为 `1`。

```jsonc
{
  "case_version": 1,
  "case_id": "case-two-cohorts-shared-participant",
  "active_linkage": "L2",                       // 可选；缺省取最晚 effective_from

  "participants": [{"participant_id": "P-ADA"}],
  "cohorts":      [{"cohort_id": "C-ALPHA"}],
  "purposes":     [{"purpose_id": "U-SUMMARY"}],

  "members": [                                  // 不可变的来源成员记录
    {"member_id": "M-A1", "cohort_id": "C-ALPHA"}
  ],

  "snapshots": [                                // 带版本的多集
    {"snapshot_id": "S-ALPHA-V1", "cohort_id": "C-ALPHA", "version": 1,
     "members": ["M-A1", "M-A2", "M-A1"]}       // 重复出现只计一次暴露
  ],

  "windows": [                                  // 左闭右开 [start, end)
    {"window_id": "W-Q4", "start": 0, "end": 10,
     "purpose_ids": ["U-SUMMARY"], "cap": "1"}
  ],

  "releases": [                                 // 已绑定、不可复用 ID 的发布
    {"release_id": "R-ALPHA-1", "batch_id": "B-2026Q4",
     "snapshot_id": "S-ALPHA-V1", "purpose_id": "U-SUMMARY",
     "occasion": 3, "cost": "1/3",
     "status": "published"}                     // published | retracted | draft
  ],

  "candidates": [                               // choose 的待发布候选
    {"release_id": "CAND-1", "snapshot_id": "S-CAND-V1", "purpose_id": "U-SUMMARY",
     "occasion": 7, "cost": "1/4", "research_value": "5", "mandatory": false}
  ],

  "linkages": [                                 // 随有效期演进的对照表
    {"linkage_id": "L1", "effective_from": 0, "effective_to": 100,
     "assignments": [
       {"member_id": "M-A1", "participant_id": "P-ADA"},          // 确定映射
       {"member_id": "M-A2", "participant_ids": ["P-BOR","P-CYR"]} // 候选解释
     ],
     "merge_groups": [["M-A1", "M-A2"]]         // 这些旧号是同一个自然人
    }
  ],

  "audit_views": [{"view_id": "V-ORIGINAL", "linkage_id": "L1"}],
  "recheck": {"view_id": "V-ORIGINAL", "new_linkage": "L2"}
}
```

### 有理数

所有成本、上限、研究价值都是**精确有理数**，可写成 `"3"`、`"-1/2"`、`"1/3"`、`"0.25"`、`"1e-3"`。JSON 里的浮点字面量在解析时按**字面文本**转成十进制有理数（如 `0.6666666666666666` → `3333333333333333/5000000000000000`），绝不经过二进制浮点。因此 `1/3 + 1/3 > 0.6666666666666666` 恒成立——**严格超预算不会被浮点近似当作合规**。成本不得为负。

### 字段约束（要点）

* **发布 ID 不可复用**：`releases`、`candidates` 内部及两者之间都不得重复；候选也不得复用已绑定发布的 ID。
* **`batch_id` 必填**：每个发布绑定查询批次。
* **快照绑定**：发布的 `snapshot_id` 决定它真实使用的队列快照；同一队列的 `version` 不得重复。
* **窗口**：`end > start`，`purpose_ids` 非空且不重复；`cap >= 0`。
* **状态**：`published`、`retracted` 计入成本；`draft` 视为未提交、可退款。
* **对照表**：一名成员在同一张表里只能有一条赋值，若存在多个可行参与者请写进该条的 `participant_ids`；`merge_groups` 内每条至少两个成员、互不重叠、必须能被同一参与者满足。
* **合并组**：其可行集合是组内各成员候选集的交集；交集为空时在加载阶段即拒绝（`infeasible-merge-group`）。

---

## 合同规则如何被实现

| 合同条款 | 实现位置 | 行为 |
| --- | --- | --- |
| 发布绑定不可复用 ID、批次、真实使用的快照、用途组 | `casefile.Release` | 发布只引用一份快照，不引用"最新"快照 |
| 队列快照是带版本的多集，同一人在同一发布中重复出现只计一次 | `ledger.release_exposure` | 先对快照成员去重，再映射到选择点 |
| 补报历史人口变化不能重写旧发布的暴露集合 | `ledger.accounted_exposures` | 只读发布自身绑定的快照；新快照不追溯影响旧发布 |
| 身份对照表随有效期演进 | `casefile.Linkage` | 对照表带 `effective_from`/`effective_to`，可并存多张 |
| 合并后旧发布代价合并计算，但同一发布里的两个旧号也只收费一次 | `identity.build_components` + `ledger` | `merge_groups` 把旧号并成一个选择点；发布按选择点去重后收费 |
| 拆分按不可变来源成员重新归属 | `casefile.Member` | 成员记录不可变；"拆分"体现为另一张对照表把同一记录指向其他参与者 |
| 有含糊映射时按候选解释审查 | `identity.Components.admissible` | `participant_ids` 每个取值都是一种可行解释 |
| 不同发布中指向同一来源成员的选择必须一致 | `identity.Components` | 解释是**全局函数**：每个选择点在所有发布中取同一参与者 |
| 被要求撤回的发布仍按已发生成本计入 | `casefile.ACCOUNTED_STATUSES` | `retracted` 计入 |
| 只有未提交的草稿可撤回退款 | `casefile.STATUS_DRAFT` | `draft` 不计入 |
| 按用途组及时间窗对每人求精确有理数消耗 | `ledger`、`audit.assess_worst_case` | 全程 `fractions.Fraction` |
| 审计返回是否在每种可行身份解释下合规 | `audit.audit_with_linkage` | 见下文"为什么不必枚举" |
| 证明超额要给来源、发布集合、精确分数与一组完整可行身份解释 | `audit.Violation` | `sources`、`release_ids`、`charge`/`excess`、`witness` |
| 违反项按时间窗、用途、来源排序 | `audit.Violation.sort_key` | `(start, end, purpose_key, window_id, source_id, participant_id)` |
| 窗口左闭右开 | `casefile.Window.matches` | `start <= occasion < end` |

### 为什么不必枚举就能在"每种解释"下合规

一次身份解释给每个**选择点**（同一自然人被合并后的等价类）指派一个可行参与者。选择点之间相互独立，所以：

> 固定参与者 `p`，把每个"可接纳 `p`"的选择点都指派给 `p`，是一个**可行**解释；它让 `p` 的暴露集合达到最大，且支配其它任何解释。

因此 `p` 在某个窗口的**精确最坏消耗**，可由"朝 `p` 定向"的单次解释一次算出，无需枚举。快速审计对每个窗口、每位参与者分别取这个最坏值；只要有一个超过上限，该解释就构成超额的可行证明。`verify` 则完整枚举所有解释独立复核。

复杂度：`候选数 × 窗口数 × 选择点`，可轻松支撑 200 个窗口、30 个选择点、20 个候选。

---

## 输出结构

### `audit`

```jsonc
{
  "ok": true, "command": "audit", "case_id": "...",
  "view": {"view_id": "V-ORIGINAL", "linkage_id": "L1"},
  "compliant": false,
  "interpretations": 2,                 // 可行解释总数
  "accounted_releases": ["R-ALPHA-1", ...],
  "refunded_releases": ["R-DRAFT-1"],   // 仅 draft
  "windows_with_violations": 1,
  "violations": [
    {
      "window_id": "W-Q4", "start": 0, "end": 10,
      "purpose_ids": ["U-SUMMARY"], "cap": "1",
      "participant_id": "P-ADA",
      "source_id": "M-A1",              // 首个抽象来源（选择点代表成员）
      "sources": [                      // 该参与者在本窗口的全部来源
        {"source_id": "M-A1", "member_ids": ["M-A1"],
         "cohort_ids": ["C-ALPHA"], "release_ids": ["R-ALPHA-1"]}
      ],
      "release_ids": ["R-ALPHA-1", "R-BETA-1", "R-RETRACTED-1"],
      "charge": "13/10",                // 精确分数
      "excess": "3/10",
      "witness": {"M-A1":"P-ADA", "M-A2":"P-BOR", "M-B1":"P-ADA", "M-B2":"P-CYR"}
    }
  ]
}
```

`witness` 是覆盖**全部已解释成员**的完整可行身份解释，按成员 ID 排序；它正是使 `participant_id` 达到 `charge` 的那次解释。

### `recheck`

```jsonc
{
  "ok": true, "command": "recheck",
  "view": {"view_id": "V-ORIGINAL", "linkage_id": "L-ORIGINAL"},
  "new_linkage": "L-AMBIGUOUS",
  "old": {"linkage_id": "...", "compliant": false, "interpretations": 1, "violations": [...]},
  "new": {"linkage_id": "...", "compliant": false, "interpretations": 2, "violations": [...]},
  "changed": [
    {"change": "introduced", "window_id": "W-Q4", "participant_id": "P-CYR",
     "source_id": "M-A1", "new_charge": "13/10", "new_excess": "3/10"}
  ]
}
```

`changed` 只有三类：`introduced`（新表下新增超额）、`resolved`（新表下不再超额）、`revised`（仍超额但精确分数或发布集合变化）。

### `choose`

```jsonc
{
  "ok": true, "command": "choose", "case_id": "...",
  "feasible": true,
  "mandatory_release_ids": ["CAND-MUST"],
  "selected_release_ids": ["CAND-A","CAND-D","CAND-MUST"],
  "optional_release_ids": ["CAND-A","CAND-D"],
  "research_value": "7",
  "added_worst_case_consumption": "1/2",
  "baseline_compliant": true,
  "candidates_considered": 5
}
```

无可行子集时 `feasible:false`，`reason:"no-subset-within-budget"`，并在 `blocked` 中列出第一个无法满足的 `(窗口, 参与者)` 单元及其精确用量、上限与超额。

### `verify`

比较快速审计与独立穷举的每个 `(窗口, 参与者)` 单元：`choices_enumerated`、`fast_compliant`、`bruteforce_compliant`、`agree`、`mismatches`。

---

## `choose` 的选择规则

满足以下词典序目标：

1. **可行性**：保留全部强制候选；对**每个** `(窗口, 参与者)` 单元，基线消耗 + 选中候选消耗 ≤ 上限。这等价于"任一身份解释下都合规"（同一单调性论证）。
2. **最大化**研究价值总和（`research_value` 来自输入）。
3. **最小化**新增的最坏个人消耗（即被选中候选在各单元上、连同强制项在内的最大增量）。
4. **最小发布 ID 列表**（排序后词典序）。

实现为多维 0/1 背包的精确分支定界，非启发式。20 个候选实例在数十毫秒内求解。

---

## 测试与回归

断言测试使用 `unittest`：

```bash
# 全量
python -m unittest discover -s tests -t .

# 分模块
python -m unittest tests.test_accounting -v     # 去重、撤回、退款、窗口边界、精确性
python -m unittest tests.test_identity -v       # 合并、拆分联合选择、候选解释、跨发布一致性
python -m unittest tests.test_bruteforce -v     # 快速审计 ↔ 独立穷举一致性（含 40 组随机用例）
python -m unittest tests.test_choose -v         # choose 的可行性与三级目标
python -m unittest tests.test_recheck -v        # 历史视图重放与结论变化
python -m unittest tests.test_validation -v     # 字段级错误定位与 CLI 确定性
python -m unittest tests.test_regressions -v    # 合同点名的五类回归
python -m unittest tests.test_scale -v          # 200 窗口 / 30 选择 / 20 候选与排序
```

`tests/test_regressions.py` 逐条覆盖题目点名的场景：

1. **合并后旧账同一次去重** — 两个旧号各自的发布 + 一份同时引用二者的发布，合并后合计精确，且那份发布只收一次费；
2. **拆分时需共同选择** — 同一来源成员跨两个发布必须在两个发布中取同一参与者（2 种解释而非 4 种），否则会低估超额；
3. **恰在窗末** — `occasion == end` 落在窗口外，`occasion == end-1` 在内；
4. **撤下已发报表不返还** — `retracted` 仍计入，只有 `draft` 退款；
5. **浮点近似会把严格超预算当作合规** — `1/3 + 1/3` 对上限 `0.6666666666666666`：精确判超额，浮点则判合规。

---

## 目录结构

```
release_budget/
  rational.py    精确有理数解析与规范化输出
  errors.py      带字段路径与记录 ID 的结构化错误
  casefile.py    案例模型、加载与校验
  identity.py    选择点（合并等价类）与全局身份解释
  ledger.py      暴露集合、精确消耗、最坏情形
  audit.py       audit / recheck 报告
  choose.py      候选子集的精确优化
  bruteforce.py  独立穷举与一致性校验
  jsonout.py     确定性 UTF-8 JSON
  cli.py, __main__.py
cases/
  case.json      两份各自合规、联发超额的最小两队列案例
  choose.json    基线合规、候选共享来源成员的 choose 案例
tests/
```
