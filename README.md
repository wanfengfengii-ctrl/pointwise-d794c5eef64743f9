# 青白河观测日账湖

青白河流域监测资料中心用这套本地程序交接单日观测账。白班根据站端晨册和更正单冻结日账，归档员发布指定版索引并准备夜班来件，夜班在与来源库分离的隔离库按站复核。索引可早于、等于或晚于目标日版，运输缺页时仍能核账无关站。

## 运行

使用 Python 3.11。项目入口从仓库根目录执行。完整回归入口由运行环境提供。

```text
python3 -B check_project.py
python3 -B demo.py
```

演示从 `samples/` 导入日批和更正单，在临时目录发行及运送三份来件。删除来源库后完成隔离接纳，重开隔离库后实际计算已核实明细、整数合计、空账和待证状态。每条 `POINTWISE_SCENARIO` 日志由通过实际断言的结果生成。回归另覆盖反向换版、跨两页更正、非法来件、实际提交失败及重复初始化。

## 白班到夜班

1. 站端来批提供日、观测号、站号、整数或缺测及出处。登记员校验完整来批。
2. 初版以该日第 1 版冻结。登记员按明确的同日父版交付增删、换站和更值，形成下一编号的冻结版。
3. 归档员从所请求修订发布固定索引，用相邻编号冻结版生成更正页和区间清册。
4. 归档员为同日选定锚点、目标和实交页。发行件保留完整预期清册。
5. 按件号读取并导出原字节，将文件运送给隔离库。
6. 隔离库验证来件格式、方向所需的完整区间和每份实交页，再一次提交原件。
7. 夜班按接纳号和站号查询目标修订，领到完整明细、确证空账或待证结果。
8. 夜班核对读值、出处、缺测数和整数合计。后续来源更正和隔离库重开保留原接纳件的结果。

## 初始化和公开 API

`SourceStore(path)` 和 `ReceivingStore(path)` 分别打开两个不同的 SQLite 文件，均支持 `with` 关闭连接。来源先调用 `SourceStore.initialize()`，随后调用 `initialize_station_roster(source, stations)`、`initialize_indexes(source)`、`initialize_page_history(source)` 和 `initialize_dispatch_archive(source)`。隔离库使用 `ReceivingStore.initialize()`。重新初始化保留已冻结版、索引、页、已发件和接纳件。登记站点可追加，已发索引保存发布时的名单和行。

| 接口 | 调用与结果 |
| --- | --- |
| I1 | `lake.revisions.intake.intake_batch(source, day, observations)` 返回 `DayRevision`。`Observation(observation_id, station, value, source)` 可从 `lake.revisions.observations` 导入。 |
| I2 | `lake.revisions.amendments.freeze_amendment(source, day, parent, amendments)` 返回新 `DayRevision`。`Amendment` 的动作是 `add`、`replace` 或 `delete`。前两者提供完整替换行，删除省略替换行。每批只对同一观测执行一个动作。`load_revision(source, day, number)` 从 `lake.revisions.snapshots` 读取固定版。 |
| I3 | `lake.indexes.publish.publish_index(source, day, revision)` 返回整数锚点号。`load_index(source, anchor_id)` 返回原 `StationIndex`。可用 `lake.indexes.postings.ordered_members(posting)` 取索引中的观测号序列。 |
| I4 | `lake.pages.history.seal_interval(source, before, after)` 接受有序的同日相邻 `DayRevision`，返回固定 `IntervalManifest`。`load_interval_manifest(source, day, lower, upper)` 和 `load_page(source, page_id)` 读回封存内容。无更正时清册条目为空。 |
| I5 | `lake.dispatch.issue.issue_handoff(source, day, anchor_id, target_revision, page_ids=None)` 返回已提交的整数件号。`None` 选全页，空序列选零页。`lake.dispatch.archive.load_issued(source, handoff_id)` 读取原字节。`lake.dispatch.transport.export_issued(source, handoff_id, path)` 导出至指定路径，`read_delivery(path)` 读取运送副本。 |
| I6 | `lake.receiving.accept.accept_handoff(receiving, body)` 接受运送件字节并返回本库接纳号。每次成功接纳拥有独立接纳号。 |
| I7 | `lake.receiving.lookup.lookup_station(receiving, receipt_id, station)` 返回 `StationReceipt`。结果绑定接纳号、件号、日、目标修订及站号。 |

日批键采用 `YYYY-MM-DD`。观测号、站号及出处为可打印、首尾无空白的非空文本。出处由站端提供，例如 `青站晨册#b`。读值为 Python 整数或 `None`。修订编号按日的冻结顺序分配。父版指定勘误基准，相邻页比较实际编号相邻的两份冻结快照。

## 计算与失败语义

索引按实际站别分组并嵌入可定位的原行，观测号按 Unicode 码点排列。登记空站保留空的索引条目。完整锚点也可证明其他合法站号在该版无观测。更正页按观测号比较前后行，每个有变化的观测独占一个确定编号的封存页。清册记录前后站的并集及规范正文的 SHA-256 摘要。页正文区分一侧没有观测和观测存在但读值缺测。

V1 交接外层为 UTF-8 JSON，字段包括 `version`、`handoff_id`、`day`、`anchor_id`、`anchor`、`target_revision`、`manifests`、`delivered_pages`。读值在交换和来源库存中使用带可选负号的规范小写十六进制文本或 `null`，以保全 Python 整数精度。公开 Python 接口直接返回整数或 `None`。摘要绑定实交正文和件内固定清册，运送的业务前提是归档员提供的清册为准。

领取核对查询站的缺页清册，再按方向使用更正页的对应行，得到目标版的成员、读值和出处。`complete` 带有序 `rows` 和 `totals`。确证空账的 `rows` 为空元组且合计为 0。`insufficient` 带 `missing_pages`，此时 `rows` 和 `totals` 为 `None`。整数以站端原始计数单位相加，缺测留在行内并跳过求和，0 参与计数。

重复身份、非法出处、非法更正、异日材料和来件失配均拒绝相应操作。无效值抛出 `ValueError`，缺失的已冻结记录、应封存的区间、已发件或接纳号抛出 `KeyError`。文件读写和 SQLite 错误沿原异常传播。事务失败回滚，无新增冻结修订、发行件或接纳记录。来源继续更正、重开或初始化后，已接纳件仍从原字节领取。仓库内每次自动验证使用独立临时目录。
