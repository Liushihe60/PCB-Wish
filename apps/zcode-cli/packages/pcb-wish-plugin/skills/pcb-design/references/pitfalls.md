# 嘉立创EDA专业版平台陷阱清单（持续维护，动手前必读）

每一条都来自真实项目的翻车记录（pcb-skill / easyeda-agent / 调研结论）。规则：遇到符合描述的场景，先按对策执行；发现新陷阱，append 到本文件并在决策日志记录。

## 电气类

1. **net port 静默并网**：net port 连接规模超过平台内部阈值（实测 180–263 端子之间）时大规模静默并网，DRC 不报错。对策：跨图页/跨模块连接用 net flag（网络标签）而非 net port；整板验证必须跑独立网表断言。
2. **sch_run_drc 异步返回旧结果**：DRC 是异步任务，立即读取可能返回上一次的结果。对策：调用后等待 5-10 秒再取结果；关键判定前后各取一次比对时间戳。
3. **uniqueId 分配冲突**：读取网表时 uniqueId 冲突会导致网络被改名。对策：网表比对前先保存并重开工程复核。

## 库与几何类

4. **库文档 y 轴与画布 y 轴方向相反**：按库文档坐标放置会整页镜像（后果：MOSFET 源漏互换），DRC 不报错。对策：引脚坐标只从已放置实例读回；每个封装用"已知不对称测试体"校准一次再批量用。
5. **4 层板内层 layerStatus=0**：内层状态为 0 时实际不可布铜，界面上却显示存在。对策：叠层设置后逐一回读各层 layerStatus。
6. **填充避让规则**：编号靠后的填充避让靠前的。对策：多填充区域时按预期优先级规划创建顺序。

## 验证类

7. **DRC=0 不是"板子正确"的证据**：DRC 只查规则违规，查不出意图错误（短路、漏连、keepout 无效等）。对策：独立网表断言 + Gerber 复核 + 冷重启复测三件套。
8. **无编程化 undo**：所有写操作不可撤销。对策：写前守卫（目标/器件身份/影响范围检查）+ 写后回读 + 审计日志；关键操作前先 save。
9. **叠层参数读不到**：介电厚度/Er/铜重 API 读不全。对策：阻抗相关结论只能标"设计期规则满足"，实际阻抗由打样厂阻抗条带验证。

## 流程类

10. **会话与客户端绑定**：CLI/MCP 通道依赖运行中的桌面客户端（一个窗口=一个会话），浏览器/在线版不支持。对策：会话管理器模式（open/invoke/session close），崩溃后用 resume 恢复。
11. **批量大小**：每批 20-25 器件，避免超时；invoke 默认超时 60s，长任务显式调大（上限 30 分钟）。
12. **手写 eprj3 的坑**：id 须 16 位小写 hex 全局唯一、ticket 唯一、原理图角度需 `(360−API角度)%360` 换算、落盘后等 10-15 秒让编辑器重载复核。

## CLI/invoke 驱动类（2026-10-19 flexiheal-v1 实测新增）

9. **sch_PrimitiveComponent.create 首参数必须传器件对象**：传裸 UUID 字符串会内部挂起、invoke 超时（画布无产物、无报错）。对策：`lib_Device.search()` 的结果项直接作为首参；`doc api --class-name SCH_PrimitiveComponent --method-name create` 查签名（注意 doc 类名是大写前缀 SCH_/DMT_/LIB_，运行时命名空间是小写 sch_/dmt_/lib_）。
10. **放置/画布操作前必须先激活页签**：`open --headless true` 会话永远 notLoaded 且画布 API 内部报 "Cannot read properties of undefined (reading 'map')"。对策：GUI 会话 → `openProject` → `dmt_EditorControl.openDocument(<页面uuid>)` → 再读写图元；`getReadyState 'notLoaded'` 是红鲱鱼，以实际操作结果为准。
11. **BETA API 响应可能丢失**：invoke 可能超时但操作已执行，也可能真未执行。对策：一律"独立读取回读确认"（getAllPrimitiveId/get），不信返回值；批量放置每件后回读位号/坐标。
12. **createProject/openProject 的 waitForReady 不阻塞**：立即返回 notLoaded，不等待文档加载。对策：自建轮询（操作失败重试）而非依赖 waitForReady。
13. **组件状态不含引脚坐标**：getState_Component() 只回 {libraryUuid,uuid,name}；引脚级数据走 sch_PrimitivePin.getAll()（放置后异步可读）或 getPrimitivesInRegion。回读为 0 时先确认页面已渲染再取。
14. **BETA API 画布读数滞后（分钟级不一致）**：删除/创建后 `getAllPrimitiveId` 可能长时间返回旧数据（实测：删除 80 件后仍读到 79 件，44s 后才稳定为 0）。对策：任何"画布为空/已清空/已放置"的判定，必须连续 ≥3 次读数（间隔 ≥8s）一致才算数；放置核验用画布审计（计数+去重+位号），**不用网表**（网表反映已保存且布线后的状态）。
15. **库内选型的 token 纪律（型号冻结红线）**：模糊关键词的首条命中会冒充目标器件（实测：搜 'NRF52840' 首条是 E73 模组——描述含 "nrf52840"；泛型 "0402" 会被电位器/端子/钽电容/薄膜电容冒充）。对策：型号冻结器件的过滤 token 必须含**封装/型号后缀特征**（如 'qiaa'）；阻容一律走泛型器件（名为 "0402"/"0603" 的系统泛型 + 值属性），不做值搜索匹配；`bom_finalize.py` 禁止宽松回退，未命中即列待人工。
16. **探测 Electron 客户端会误启动 GUI**：对 `lceda-pro.exe` 传 `--help` 等非文档化参数会直接拉起客户端窗口（产生 7+ 进程）。对策：只使用文档化参数（doctor/open/invoke/doc/session/stop）；误启动后 `taskkill /IM lceda-pro.exe /F` 清理；批量任务被中途取消时，续跑前先探画布稳定态再决定。

## 布局几何类（两层纪律）

17. **焊盘自转角（prot）必须入模，几何管线必须局部系统一**：`getAllPinsByPrimitiveId`/`pcb_PrimitivePad.get` 的 `pad:["RECT",w,h,0]` 是焊盘**本地轴**，焊盘原语自带画布绝对 `rotation`（X2 壳脚=90°：横置 6.2×2.5 被按竖置建模，x 向虚减 3.7mm，**C7 焊盘×X2 壳脚真实重叠漏判**）。且 dump 焊盘相对坐标是画布轴系、丝印原语是局部系——混框 + 旋转件双转是系统性误差。对策：dump 导出 prot；`pcb_bodies_lib` 统一"局部系 union → 一次施加组件旋转"（local_pads_union_mil/cover_local_mm/cover_abs_mm），求解器/检查器全走该管线。
18. **位号文本=丝印（L3 Attribute 原语，自带画布绝对坐标）**：`pcb_PrimitiveComponent.modify` 挪件**不会**带走位号文本——多轮搬移后文本脱位 3.5~6.2mm（J1-J5/U1-U3/L1/L2/P3/X2 实测），压他件丝印/焊盘（曾产生 82 对文本违纪）。对策（**2026-10-22 G3T 站修订，原"强制位号回中"作废**）：位号文本**必须落在自身器件的丝印划定范围之外**——器件本体中心必在自身丝印轮廓内，"文本=组件中心"式回中是**必然制造缺陷**的动作（flexiheal-v2 板 23/23 个位号因此全部压在自家丝印上）。落位脚本末尾必须跑 `pcb_text_place.py` **归位**：判据 = ①文本盒不与**该件**丝印轮廓相交（他件同理）②不压任何焊盘（含自身）③板内且距板框 ≥0.3mm ④盒尺寸=0.65×字号×字符数 × 字号；搜索序 = **先排除自身丝印禁区**，再在四周候选中选（上/下/左/右→四对角 × 步长 0.3/0.5/0.8/1.2/1.8/2.6mm × 缩字 45→30→20mil × 横排→竖排），**「中」候选与任何与自身丝印相交的候选直接剔除**；长位号（文本盒宽于自身覆盖区，如 R_PROG/R_refj1/R_refj2）同此流程，不再单列特例。文本层违纪按 gap0 口径判（gap0.5 的 0.25 外扩环下文本穿邻件是噪声），**但"文本 vs 自身丝印"不因口径而豁免**。
19. **丝印与焊盘失位=库缺陷必须报备**：X2 库封装丝印盒(1.3×2.8)与焊盘 hull(10.45×3.54)完全脱位、位号文本锚点偏移(−6.15,−1.89)压自家壳脚——"丝印划定范围"口径对此类封装失效，覆盖区会退化为焊盘 hull。对策：检查器对"丝印盒/焊盘hull 轴向占比 <0.35 且交叠异常"输出审计注记报提问单，等换封装；不得静默采信。

## 布线类（2026-10-20 flexiheal-v1 G4 实测新增）

20. **save 会整体重排画布图元 id**：一次 save 后 getAllPrimitiveId 返回的 id 全部失效（实测 22 颗过孔 save 前后 id 全换），"按 id 回读/删除"必须在 save 后重新 enumerate；判定写操作成败以"重 enumeration + 位置/属性匹配"为准，不依赖历史 id。
21. **startAutoRouting 作业版为非功能存根**（多种 props 均返回 {jobId:""}），同步 autoRouting(props) 可用：RoutingNets/ignoreNets 有效、批量 >4 网会过载返 null（降低为 1~2 网一批+55s 冷却）；cornerStyle/optimization 枚举在 invoke 沙箱不可达，可省略；autoRouting **不读网级规则宽度**（输出恒为内部默认 0.254/0.224mm）——布线后必须独立审计宽度与关键约束（单层/包地/总长），不满足即删网段重布或手工。
22. **pcb_Drc "Prohibited Region to Track" 判定与几何脱钩**：轨道坐标在 Region bbox 外仍被点名（实测 47 条误报且与 LED_AA 等区域无关轨道）；Region 'R' 多边形语义 = (x,y,宽,高,旋转,圆角)。天线禁走线合规判定必须独立几何审计（端点/扫掠遍历 own dump），不得以 DRC 该类别计数为证。
23. **同网走线搭过孔处 EDA 自动拆分走线**（3 段→9 段、电气等价），线数审计时按"网内连续段拼接"理解；**invoke JS 全体返回 null 但先前语句已生效**（中途 ReferenceError 不回滚）——脚本内语句分步独立回读，不能以总 null 判定整批失败；pcb_PrimitiveLine/Via.delete 的 primitiveIds 必须传数组形式（单 string 抛 TypeError）；过孔 hole 尺寸 <12mil 被 Via Size 规则命中（A* 扇出孔统一 hole≥12）。

## 原理图删除/ERC 类（flexiheal-v1 S2 实测新增）

24. **原理图 net flag/net port 本身是组件原语**：`componentType='netflag'`，带 x/y/net 字段，删除走 `sch_PrimitiveComponent.delete(id)`（单 id 形参可用）；删除旗标会**级联清除其引脚坐标处的零长度桩线**（wire 枚举同步减少），无需也不能再手动 `sch_PrimitiveWire.delete` 同坐标桩线。旗标↔引脚归属按"旗标坐标 == 引脚坐标"精确匹配，无孤儿旗标时"逐网挂脚旗标数 == 网表该网成员数"可作对账口径。
25. **sch_Drc.check 无参返回布尔、带参返回完整告警列表**：`check(true,false,true)` 同步返回 `[{type:'warn'|'error', rule, primitives:[{designator,name,primitiveId}]}]`——原理图 ERC"0 错误+warning 逐条解释"以此为准。悬空脚全集可由画布独立推导（全部器件引脚 − 挂脚旗标坐标）与 ERC 输出做双向精确对账，防 ERC 静默漏报。
26. **Windows+MSYS 混合环境下 /tmp 路径映射分裂**：bash 重定向与 MSYS 工具解析的 /tmp 同 Windows Python 解析的 `'/tmp/...'` 可能落到不同目录，invoke 输出"看起来丢失"而操作实际已执行。跨工具中间产物一律落项目目录显式路径；判别"API 未执行 vs 文件没找到"以画布独立重枚举为准，禁止凭输出缺失断定未执行而重放写操作。
27. **pcb_* API 只作用于"当前活动文档"，切页走标签树 API 而非猜 uuid/轮询**：会话活动页停在原理图时，pcb_* 系列全部报 `Cannot read properties of null (reading 'map')`——非会话故障。正路：`dmt_EditorControl.getSplitScreenTree()`（一次返回全部打开标签：tabId=`<页uuid>@<工程uuid>`、data 含 data-model-sheet-uuid/doctype，1=原理图 3=PCB）→ `activateDocument(tabId)` 激活目标页；**页 uuid 无须猜测**——标签树就是页清单。方法名反射（Object.keys/getOwnPropertyNames）对 Proxy 命名空间一律空手而归，查方法名用 `doc api --class-name DMT_Xxx`（类名大写前缀）。每站开工第一步：getSplitScreenTree 确认目标页 + activateDocument。flexiheal-v1 本工程：原理图页 cb31b4620feeec13、PCB 页 1b70bcd67c184b71（随 capsule 携带）。

## PCB 删除/截图类（flexiheal-v1 S3 实测新增）

28. **PCB 组件删除会级联带走其 L3 位号文本**（与原理图侧"文本独立需单独删"的直觉相反，也与"modify 不跟随"（#18）不对称——delete 级联、modify 不跟随）：`pcb_PrimitiveComponent.delete` 后该件 Designator 文本原语同步消失，无需也不能再按 parentPrimitiveId/value 补删（补删匹配=0 命中属预期，不是漏删）。对账口径：删后全页可见文本数=幸存组件数、文本 value 集合=幸存位号集合，双集合精确相等即无孤儿文本。
29. **getCurrentRenderedAreaImage 只回固定全画布合成视角，视口 zoom/图层选择对其无效**：`dmt_EditorControl.zoomToRegion/zoomToAllPrimitives` 与 `pcb_Layer.selectLayer/setLayerVisible` 均返回 true 但截图字节不变（同 MD5）——该 API 渲染的是"全板 fit-all+多层合成"缓冲，刷新只随画布内容变化。缩放/分层截图只能离线裁剪全图（按板框像素标定仿射映射，注意渲染 y 轴=image top=canvas 高 y）或走窗口捕获旧路（PrintWindow，D009 弃用方案）；delete/modify 传参兜底与"输出丢失勿重放"沿用 #11/#26。
30. **selectLayer = 选中并单层显示，会把其余层全部置隐藏**：`pcb_Layer.selectLayer(x)` 执行后其余层 layerStatus 变 2（显隐口径 SHOW=1/隐藏=2，getAllLayers 读回），画布只剩单层铜——用户看到"元件消失/只剩焊盘走线"（实测：s3_shot.py 截图后遗留单层态）。对策：截图脚本用 `getAllLayers()` 快照全部 layerStatus → selectLayer 拍摄 → 逐层 `setLayerVisible(id)`/`setLayerInvisible(id)` 恢复；禁止 `selectLayer(1)` 式伪恢复。标准可见集：1/2/3/4/5/6/11/47/57（57=Ratline 飞线层，隐藏时看不到飞线）。

## PCB 图元字段类（flexiheal-v1 S3D 实测新增）

31. **pcb_PrimitiveLine/Via.get 返回字段名与历史脚本口径不符时静默 undefined**：本版实测 Line 返回 `startX/startY/endX/endY/lineWidth`、Via 返回 `holeDiameter`——历史脚本按 `x1/y1/width/hole` 取值得到 undefined，JSON 序列化时键被静默丢弃（不报错），下游拿到"缺几何字段的空壳记录"若不校验就会当成 0/NaN 入库。对策：dump 后必须校验几何字段非空（任一 null/undefined 即失败）再入库；字段名以当场 `get` 全量回读为准，不假设历史键名（平台升级会改）。

## 布线与引擎类（flexiheal-v1 S4E 实测新增）

32. **自研 A\* 网格求解器产物天然 90° 阶梯拐角**（用户判"布线失败"的直接原因）：A\* 在正交网格上搜索，路径由轴向步组成，转向必为 90°；即使把阶梯路径"直线化"合并成两段，也会得到 90° 直角，且直线化会让切角斜穿焊盘。对策：**布线优先级倒置**——平台原生路由器优先，自研求解器降为兜底；兜底产物必须做 45° 倒角整改（切角量 d=min(可用余量, 线宽×1.5)，改完必须重跑间距校验）。同时修两个求解器缺陷：①输出段合并只允许同方向延伸（不得把阶梯直线化）；②求解器网格 eps 档与验收门必须同口径（JLC 间距、L2 小外形 SMD 潜渡豁免），否则求解器比验收门更严会误判 blocked。

33. **批量删线后 autoRouting 静默失效（返回 undefined、零产物、不报错）**：一次 invoke 内批量删掉数百段走线后，`pcb_Document.autoRouting` 立刻变成"空转"——返回值 `undefined`、耗时 7~17ms、画布零新增，且不抛异常；各种 props 组合（cornerStyle/optimization/existingPrimitiveMode/layers/selected）全部无效，造一条临时走线也无用。**根因**：文档内路由器状态在批量删线后未复位。**修法**：`dmt_EditorControl.closeDocument(tabId)` → `openDocument(<页 uuid>)` → `activateDocument(tabId)`，复位后 autoRouting 立即恢复（返回 `{success:true,totalNetsCount,successNetsCount,failedNets,duration}`）。纪律：批量删线后必须先重载文档再生效；任何 autoRouting 调用先判"返回值对象含 `success` 字段"才算路由器真的跑过。

34. **autoRouting"跑过但该网失败"由返回值 failedNets 表达**：真实不可解网返回 `{success:true, successNetsCount:0, failedNets:['DECUSB_N']}`，耗时 10s 级（而非毫秒级空转）。判据口径：**返回值含 success 字段 = 引擎跑过**；再读 `failedNets` 得真实不可解网清单；"画布有无新线"只作第二口径。三者混用会把"真实不可解"误判成"引擎静默"并触发无谓重试风暴。

35. **作业模型 auto-routing 全部 API 均为存根**：`startAutoRouting(props)` 恒返回 `{jobId:""}`；`getAutoJobStatus(x)` 恒 `undefined`；`getAutoJobLog(x)` 恒 `[]`；`cancelAutoJob(x)` 恒 `false`。**只有同步版 `autoRouting(props)` 可用**（补 #21）。判据：作业模型不可作为引擎选型的备选分支，也不要把 `getAutoJobStatus` 返回 undefined 误读为"调用失败"。

36. **DRC 条目的真 primitiveId 在顶层 `objs` 数组，不在 `obj1.suffix`**：`obj1.suffix` 形如 `"(GND): e120"`，其中 `e120` 是**全局序号**不是图元 id；拿它当 primitiveId 传给 `pcb_PrimitiveVia.delete` 会整批抛异常（返回值 `["E","E","E","E","E"]`）。真 id 在条目顶层 `objs: ["4b722f2fd02cf069"]`（与 `errData.obj1` 同值）。删废孔必须取 `objs[0]`。

37. **DRC 原始返回是两层树**：`[{name:'Connection Error', list:[{name:'<网名>', list:[条目]}]}, {name:'Clearance', ...}]`——顶层是**违例类别**，第二层是**网名**，第三层才是条目。直接对顶层做类型统计会得到 "type=''" 的垃圾口径。条目真字段：`errorType`（类别）、`ruleName`、`net`、`errorObjType`、`objs`（真 id 数组）、`errData.obj1Suffix`（全局序号）、`pos`（画布坐标，单位 mm）。

38. **删线后网级线宽规则会丢，必须重覆写并抽查**：清场删除走线后 `pcb_Drc.getNetRules()` 里的网级 Track 宽度会回到 default（VBUS 1.0/VDD 0.6/ADC_IN 0.3 全丢）。清线后必须跑 `pcb_route_from_plan.py --phase rules` 覆写并回读抽查三条关键值。注意画布网名与 plan 网名可能差大小写（plan `DEC5b_N` vs 画布 `DEC5B_N`），需按别名映射补写。

39. **覆写走线宽度/孔径的可用手段**（BETA，已探明签名）：`pcb_PrimitiveLine.modify(idOrObj, {net, layer, startX, startY, endX, endY, lineWidth, primitiveLock})`（id 传字符串会先 `get(id)`）；`pcb_PrimitiveVia.modify(idOrObj, {net, x, y, holeDiameter, diameter, viaType, designRuleBlindViaName, solderMaskExpansion, primitiveLock})`（`solderMaskExpansion` 必须传对象）。改宽度后必须回读 + 跑 DRC 增量核查，不得凭返回值判成功。

40. **`pcb_Net` 长度口径高估连通**：`pcb_Net.getAllNets()[].length` 把"该网所有走线长度"当连通判据，断连网也可能有非零长度。判定"已布"必须用几何口径（段端/焊盘/过孔/T 搭的连通性 union-find，见 `s4_analyze.py`），`pcb_Net` 长度只作第二口径。自动布线产品同样适用：路由器报 success 也不等于全网连通（终点未触焊盘的情形需几何复核）。

41. **批量删线 + 重建图元后 EDA 的飞线/连接状态不刷新 → DRC Connection Error 虚高**：本站用"删全部非 GND 走线 → 从快照逐段重建"做回滚/重布后，`pcb_Drc.check` 报 Connection Error **192** 条（几乎每网都被点名），而几何口径 union-find 显示只有 11 个焊盘未连。`closeDocument → openDocument → activateDocument` 复位后同一 DRC 立刻回落到 **86** 条 = GND 64 + 11 网×2，与几何口径逐网完全一致。对策：**任何"连接类"DRC 数字必须先做一次文档重载再取**；同理"批量重建图元"之后不要相信 DRC 的连接类结论，先复位再判定。

42. **`pcb_PrimitiveLine.modify` 传陈旧 id 会抛 `Cannot read properties of undefined (reading 'isAsync')`**：`modify(id, props)` 内部先 `get(id)`，id 失效（save 后 id 全变，pitfalls #20）时 `get` 返回 undefined → 解构 `setState_*` 前即崩，**该图元被静默跳过**（不是被删，但会让"按 id 批量核对"失真）；批量改宽度时若把 `startX/endX/lineWidth` 等字段一次性传给几十~上百个陈旧 id，崩溃条目会混入结果且难以察觉（本站一次加宽 137 段，3 段崩、画布线数从 423 掉到 419）。对策：改宽度/改端点前**重新枚举取当前 id**，或改用"删除 + 重建"（`pcb_PrimitiveLine.create` 的 id 由平台分配，无陈旧问题）；改完必须回读线数与线宽分布。

43. **几何校验的"线段-线段距离"不能用包围盒近似**：`pcb_route_lib.seg_seg_dist` 以对方线段的**轴对齐包围盒**近似扫掠区，对 45° 斜线段会系统性低估到 0（本轮误报 34 条"距离 0.0"的假违例）。对策：与求解器同口径（沿本段中轴细采样 → 到对方中轴距离 → 减半宽和），采样步长 ≤0.03mm；同理"线段-焊盘矩形"距离若只取 5 个采样点会漏掉长线段中段的最近点（把"有违例"误判成"无违例"，本轮据此错误地放行了 137 段加宽）。

## 铺铜 / 缝合 / 网表清理类（2026-10-21 flexiheal-v1 G5 实测新增）

44. **`pcb_MathPolygon.createPolygon(['R',X,Y,W,H])` 的 Y 不是画布坐标，且矩形是 `y∈[Y-H,Y]`（向上生长）**：该多边形帧的原点在**板的顶端**（canvas y=1181），且 X 为矩形左边界、Y 为矩形上边界；直接按画布坐标填 Y 会把铺铜放到错误象限（实测：按画布坐标填 → 铺铜只落板右上象限，GND 连接错误 64→57；换算后 → 64→18）。**对策**：建铺铜后必须回读 `pcb_PrimitivePoured.getState_PourFills()` 的 L 点包围盒反推实际覆盖范围（填充分片坐标与 canvas 同单位），别信 `getState_ComplexPolygon()` 的回显（它只回显你传进去的源数组）。坐标换算：`canvas_y = 板高_mil − Y`；要覆盖画布 `y∈[a,b]` 应传 `Y=b, H=b−a`。
45. **铺铜图元 create 之后填充区不生成，必须显式 `rebuildCopperRegion()`**：仅 `pcb_PrimitivePour.create()` 后 `pcb_PrimitivePoured.getAllPrimitiveId()` 为空；调 `pour.rebuildCopperRegion()` 才返回填充对象（并须与 create 在同一次 invoke 内调用，跨 invoke/文档重载后返回 undefined）。另注意 `rebuildCopperRegion()` 的返回值可能是**同网其它覆铜的**填充区（多覆铜同网时读数串台），判定覆铜覆盖范围要以 `pcb_PrimitivePoured.get(id).getState_PourFills()` 为准。
46. **`pcb_PrimitivePour.modify(id,{complexPolygon})` 之后 `rebuildCopperRegion()` 永久返回 null**：改完多边形后必须 **delete + create 重建**（同 pitfall #42 的"改几何只走删除+重建"），否则填充区算不出来（实测 modify 后 rebuild 恒 null）。
47. **`pcb_Drc.overwriteCurrentRuleConfiguration` 会把当前规则配置 fork 成"自定义配置"**：内置名（如 `JLCPCB Capability(Two Layers Board)`）**不可删除也不可改名**（`deleteRuleConfiguration` 对内置返回 false），`renameRuleConfiguration` 会产生同名重复条目；想要"名字不变"只能接受配置名变为"自定义配置"。另：该 API 的 `getRuleConfiguration(内置名)` 返回**旧版扁平格式**（`form.padDivergenceSpacing` 等），而 `getCurrentRuleConfiguration()` 返回**新版 status/data 格式**（`form.singleLayerPadModel.data['1'].lineClearance`）——写回时必须用当前配置的新版路径，否则报 `Cannot read properties of undefined (reading 'data')`。
48. **DRC 的连接类判定会算上覆铜填充**：有铺铜后 GND 连接错误会大幅下降（本例 64→18），说明 DRC 的连通性引擎**把覆铜填充区当铜**；反之，DRC 条目里**不会出现覆铜图元的 primitiveId**（`objs` 里只有被违例的对方图元），不能靠"条目是否引用覆铜"来判断覆铜是否参与判定。
49. **铺铜让出带会孤立"离异网铜太近"的焊盘，且这种孤立无法用走线补救**：铺铜对异网铜的让出宽度（Zone clearance）≥ DRC 的 Zone↔Pad 间距（0.254mm）时，任何距异网铜 <0.254mm 的焊盘都被让出带包住、铺铜接不到；走线救不回（Track↔SMD Pad 只要 0.152mm，走线能逃，但**落地过孔**要求 Via↔Pad ≥0.254mm，逃逸线末端仍放不下过孔 → 整条逃逸路径作废）。**对策**：落地过孔的选址判据必须用**铺铜间隙**（本例 0.3mm，实测用 0.254mm 选点会得到"落在让出带里、与铺铜不连"的孔，表现为 GND 连接错误没消）。另：净距模型必须**包含无网络（net=""）焊盘**——漏算会直接造出 `SMD Pad to Via` / `Hole to SMD Pad` 违例。
50. **板边缝合孔选址必须用"板边内缩 + 周长相位"双口径**：铺铜后的板边区还有无网络焊盘/通孔安装孔；本例 19 颗板边缝合孔里有 1 颗压到无网络焊盘（0mil），另有 1 颗落在板边距 0.695mm 处但周长相位与相邻孔重复。**对策**：选点后必须逐颗回算"到板框距离 ≥0.3mm + 半径"与"到所有异网/无网络焊盘的净距"，并按周长相位排布（5~8mm 一颗），建完立刻跑 DRC 复核（`SMD Pad to Via`/`Hole to Hole`/`Hole to TH Pad` 是典型漏网分类）。
51. **`pcb_PrimitivePad.modify(id,{net:''})` 可清空焊盘网络归属且不影响其余图元**：用来同步"原理图侧该脚未接、PCB 侧残留旧网络名"的幽灵网，是比重建网表（`PCB_Net.setNetlist` 需 167KB 跨命令行长限制、`importChanges` 有动布局风险）低风险的第三路径；清空后 PCB 网络表条数应与原理图网表**逐张**相等（本例 44→37，与原理图 37 张全等），且 EDA 网络表里大小写变体条目（`DEC5B_N` vs 焊盘实际 `DEC5b_N`）会随之消失。

## 规格构建类（2026-10-21 flexiheal-v2 G1 实测新增）

52. **库内器件名 / 封装名 / 封装形状三者互不保证一致，选型必须以封装几何为准**：库内泛型「0603」的封装名是 `C0603`（描述串里混着 0603/1206/0805/0201/1608 等电容族关键词），而名为「R0603」的条目（uuid `83dd08d617b848c48ab33d8485d6f27e`）封装与 3D 实体却是二极管封装 `SOD-523_L1.2-W0.8-LS1.6-BI`（SC-79）。**判据**：名称只是标签，真值在 ① 封装文档源的 PAD 几何（焊盘数/中心距/层）② 3D 实体标题里的尺寸串（L1.6-W0.8 → 1608 公制 = 0603）③ 封装描述串中的尺寸关键词；三者任一与预期不符即排除该条目并写进核实报告。核对路径：`eda.lib_Device.get(uuid)` 取 `association.footprintUuid` 与 `property.otherProperty['3D Model Title']` → `eda.lib_Footprint.get(uuid)` 取封装名与描述 → `eda.lib_Footprint.getDocumentSource(uuid, libraryUuid)` 解析 PAD 记录算焊盘中心距。

53. **`eda.lib_Device.get()` 不返回 `footprintName`/`symbolName` 等扁平字段**：其 own keys 只有 `libraryType/uuid/libraryUuid/name/classification/association/description/property/subPartNames`，封装与符号挂在 `association`（`symbolUuid/symbol/footprintUuid/footprint/images`）与 `property.otherProperty`（`3D Model Title`/`Supplier Part`/`JLCPCB Part Class`/`Datasheet`）。只有 `lib_Device.search()` 的命中项才带扁平化的 `footprintName/symbolName/model3DName`。误按 search 的字段名去 get → 全 undefined 且不报错，bom.json 会静默写成 null。

54. **IR 成员 token 不接受符号引脚名里的特殊字符，须改用引脚号并留原名**：`ir_validate.py` 的成员正则 `^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$` 不接受 `+`/`-`，而库内符号引脚名含这些字符（Type-C 的 `D+`/`D-`、红 LED 的 `+`/`-`）。对策：这类器件在 IR 内统一用**引脚号**作 token，原名保留在 pinmap 的 `pinNames` 字段，G2 布线按引脚号定位。库内符号引脚名回读路径：`eda.lib_Symbol.getDocumentSource(symbolUuid, libraryUuid)` → 解析 ATTR 记录（key=`Pin Name`/`Pin Number`，用 `parentId` 配对）。

55. **同名多脚器件在 IR 内只能写一次，靠 pinmap 展开成逐个引脚号**：ESP32-C3-MINI-1 符号 53 脚里 GND 有 22 个、NC 有 14 个；导出的网表用「位号.引脚号」命名空间，IR 写 `U1.GND` 一次即可，`ir2assert.py --pinmap` 会按 `pinmap[ref][token].pinNumbers` 展开成 U1.1/U1.2/… 逐个成员，精确成员集断言才与网表同口径。**漏给 `--pinmap` 时断言只含 1 个成员 → 真实网表必然 FAIL（缺成员）**。

56. **`ir2assert --with-must-connect` 产出的 must_connect 用 IR token 命名空间，与导出网表不同口径 → 必 FAIL**：must_connect 条目直接抄 `nets[].members`（未展开，形如 `U2.VCC`），而 `netlist_assert.find_net_of` 在「位号.引脚号」网表里找不到该 token → 判 None → 整条 FAIL。对策：短路/漏连检测只用 `net_members`（它按 pinmap 展开）；确需 must_connect 时先把 token 换成引脚号再入库。

57. **网表断言必须做正反双向自检，只跑正向不算验证**：用 IR+pinmap 合成理想网表跑 `netlist_assert.py` 应全 PASS（证明断言文件可消费、成员集自洽），再人为注入短路（GND 多一个脚）与漏连（某网少一个脚）各跑一次应 FAIL（证明门能抓错），三次结果一起落盘。只测正向的断言文件在真网上可能一路 FAIL 或一路放行。

58. **datasheet 功能名与库内符号引脚名是两套命名，必须都留**：datasheet 给的是功能（ADC1_CH0 / USB_D- / RXD0=GPIO20），库内符号给的是引脚名（IO0 / IO18 / RXD0）。IR 内必须用**库内符号引脚名**（布线按符号走），功能名另存 pinmap 的 `function` 字段与 IR 的网名（跨站可读）；两者对照表是 G2 布线与 G5 复核的唯一依据，不能只留一套。

59. **换板重设计不要在原工程里清空复用**：v1 画布（59 件 / 433 线 / 109 孔 / 227 焊盘 / 2 铺铜 / 37 网）是回退路径的证据与对照基线；清空即毁证据，且漏删残留会污染 DRC / 网表 / 3D / 丝印（新旧网名部分同名）。对策：新建独立工程（PCB+原理图+网表一起新建），板框/层叠/规则参数**复制而非图元复制**；若必须复用同一工程，先用只读探针证明图元归零（lines/vias/pads/components/pours/nets 全 0）再落新图元，并保留旧工程副本。

## 规格构建类（2026-10-21 flexiheal-v2 G1b 实测新增）

60. **库内符号引脚名含小数点时，IR 成员 token 必须换成引脚号（把 D016 的规则从"特殊字符"扩到"多段名"）**：`ir_validate.py` 的成员正则 `^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$` 只允许一个点号作分隔符。nRF52 类模块的库内引脚名自带点号（`P0.02_A0_D0`/`P0.04_A4_D4_SDA`），成员 `U1.P0.02_A0_D0` 直接校验失败。对策：这类器件在 IR 内用**引脚号**作 token（`U1.1`…`U1.22`），库内原名保留在 pinmap 的 `pinNames`/`symbolPinName`，再另存官方功能名与芯片脚——不要把点号替换成下划线来"造"一个名字，那会与库内符号失去逐字对应。

61. **判"通孔焊盘"要看孔径是否 >0，不能看 hole 字段是否存在**：封装文档源里每个 PAD 都有 `hole:{"holeType":"ROUND","width":0,"height":0}`，SMD 焊盘也是这个形状。按 `bool(pad["hole"])` 统计会把**所有**顶层 SMD 焊盘算成通孔——XIAO nRF52840 库内封装正是如此（22 个焊盘全在 layerId=1、孔径全 0），"22 个 1.905mm 大通孔"的结论即由此误出。正确判据：孔径 >0 **且** layerId 覆盖多层。误判会直接导致"手焊零难度"的选型前提失效。

62. **Extended Part + 无 3D 的库内封装，本体轮廓与官方机械图可能不一致（尺寸不同或整体转 90°）**：XIAO 库内封装的 layer 48 轮廓是 17.80×20.95mm，而 Seeed 机械图是 21×17.8mm。对策：选型阶段就把"库内本体轮廓 bbox"与"官方机械图"两份尺寸都落盘，差异写进人工确认项；**落地（G3）前必须按官方 DXF/Bottom Pad Data 复核焊盘阵列**，不能把库内封装当权威。

63. **4 焊盘贴片轻触开关在库内无数据手册时，第 3/4 脚是触点还是固定脚无法判定 → 选 2 电气脚型号**：这类件在库内只有封装几何没有数据手册，4 脚版的脚角色存在两种常见排布（1+3 一组 / 2+4 一组），猜错就会把 MCU 输入脚常短或常开。对策：优先选 2 脚型号（角色无歧义），并优先大焊盘（≥2×2mm）以照顾手焊；4 脚版只在下单前拿到数据手册后再用。

64. **官方 pin map 表只列模块"引出"的脚，模块底焊盘不会出现在表里**：base 版 XIAO nRF52840 的官方表有 18 脚，库内符号却有 22 脚——多出的 SWDIO/SWCLK/BAT+/BAT- 是模块底焊盘（wiki 的 "Bottom Pad Data" 与 SWD 章节才有）。对账时如实写"库内给出、官方 base 表未列"，不要替官方补功能描述；这类脚的用法要靠 wiki 文字章节支撑。

65. **选配件会挤占基线引脚，先查该功能的"专用资源富余度"再定分配**：本案里电池电量显示只能靠外部分压链，而模块 P0.31(AIN7_BAT) 未引出，22 脚里能做 SAADC 采样的 AIN 只剩 P0.29(D3)——而 D3 在 §1.3 里被派给了可选状态灯。结论：选配链占 D3，可选 LED 让位到 D6/D7（普通 GPIO 等价）。对策：分配选配功能时按"该功能唯一/专用资源是否已被基线吃掉"排序，而不是照抄引脚分配表。

## 原理图连网/标注/截图类（2026-10-21 flexiheal-v2 G2b 实测新增）

66. **原理图的旗标/网络端口/自绘标注都不跟随器件移动**：改器件位置后，它引脚上的 net flag、net port 与自绘 ✕ 全部留在**旧坐标** → 器件变成"未连网"、旧坐标留下"孤儿旗标"，而 ERC 只报悬空脚、不会报"多出旗标"。处置：改位后必须重跑**旗标坐标 == 器件当前引脚坐标**的全量对账，删掉落在任何引脚坐标之外的孤儿旗标，再按 IR 成员在**新引脚坐标**重建（旗标可重建，不能靠拖动）。判据同 pitfall #18/#28（图元不跟随），但发生在原理图侧。

67. **平台没有 no-connect 图元 API，也搜不到"不连接"符号**：`SCH_PrimitiveComponent` 只提供 createNetFlag / createNetPort / createShortCircuitFlag / createCbbSymbol；`ESCH_PrimitiveComponentType` 虽有 `nonElectrical_symbol` 但无创建入口；库内符号搜索"不连接/NC"只命中无关器件。替代口径：用**非电气多边形** `SCH_PrimitivePolygon.create([x-r,y-r,x+r,y+r,x-r,y+r,x+r,y-r], null, 'none', 2, 0)`（4 顶点蝴蝶结=✕，无 net 字段）画叉。两个必踩的细节：①`SCH_PrimitivePolygon.create` **只接受 ≥4 顶点的闭合图形**，2 点线被拒（报"无法创建矩形图元"）；②`lineType` 是数值枚举（SOLID=0/DASHED=1/DOTTED=2/DOT_DASHED=3），传字符串会失败。铺开前先做**单脚探针**（导线数/旗标数/ERC 悬空脚数三项不变）确认非电气，再批量铺。

68. **ERC 的 CHECKFLOATPIN 警告不会被非电气 ✕ 消除**：平台仍按"该引脚未连网"报悬空脚。因此"未用脚已显式标注"的正确验收口径不是"警告消失"，而是**双向集合相等**：ERC 悬空脚集合 == 画布 ✕ 覆盖的引脚集合，且被标注的引脚确实没有任何挂网旗标。拿"警告数=0"当验收会被这条规则误判为未标注。

69. **`sch_PrimitiveComponent.create` 的首参可传纯对象 `{libraryType,libraryUuid,uuid}` → 零搜索放置可行**：`eda.lib_Device.get(uuid)` 只读回读到 `libraryUuid`（注意 pitfall #53：它**不**返回扁平的 footprintName/symbolName，只有 association/property），拼成对象直接 create 即可，不必走 `lib_Device.search()`（搜索会限流且首条可能冒充目标件）。回读用 `sch_PrimitiveComponent.getAllPinsByPrimitiveId(id)` 拿引脚号/名/坐标——这是画布侧权威引脚数据（`getState_Component()` 只有 libraryUuid/uuid/name，无引脚级信息，pitfalls #13）。

70. **网络端口旗标的图形与网名随 rotation 展开**：rotation=0 时向右、180 时向左，连接点恒在 (x,y)。默认朝向会让"器件左侧引脚"的旗标压住器件本体的引脚名/位号文字。对策：左侧引脚的 BI 端口一律 rotation=180（只改图形、不改连接点与网表），但改完**必须重跑网表导出+断言**，不能假定"只是转了个图"。

71. **`getCurrentRenderedAreaImage` 对原理图页同样只回"当前视口合成图"，且新建图元不会立刻出现在渲染图里**：4 次 `zoomToRegion` 的 PNG 与全图 MD5 完全相同（pitfalls #29 在原理图页成立）；新建多边形/文本后立即取图看不到它们，需等画布重绘。分区截图的无侵入标定口径：画布侧用 `SCH_Primitive.getPrimitivesBBox([图纸页id])` 取图框 canvas bbox（A4=1170×825，单位 10mil），图像侧检测**红色图框线**的像素包围盒，两者线性映射后用 PIL 离线裁剪。用"临时彩色多边形当标尺"的做法要避开：**指定 fillColor/lineColor 的多边形不渲染**，且会污染画布与 DRC 判定。

72. **原理图页签树里的 page uuid 与图纸页 uuid 是两个**：tabId 形如 `<页uuid>@<工程uuid>`，原理图页的 data 里同时有 `data-model-sheet-uuid`（= tabId 前缀，页面 uuid）与 `data-schematic-uuid`（原理图对象 uuid）；PCB 页的 data 里没有 page uuid，只能取 tabId 前缀。切页/打开页统一用 tabId 前缀作为页面句柄。

73. **`getCurrentRenderedAreaImage` 返回的是"当前焦点画布"的分屏缓冲，不是目标标签页的画布**：多页签/分屏打开时，`activateDocument(tabId)` 之后取图仍可能拿到**别的分屏格子**（实测：原理图页签激活后取到 PCB 画布 → 纯黑一色图；另一张原理图页却能正常取图）。同理，`closeDocument` + `openDocument` 重开过的页签可能长时间不重绘（实测重开后连续 8 次、每次间隔 10~15s 取图均为纯黑，只有把**其它页签全部关掉、让目标页独占窗口**才恢复绘制）。对策：截图前 ①核对返回图的颜色数（纯黑/单色 = 没画上，不是"内容为空"）；②必要时关掉其它页签独占窗口再取；③取图后**用颜色/像素做客观校验**（例如标定用的品红像素是否还在），别凭肉眼。

74. **库内封装的"全顶层 + 孔径 0"不一定是缺陷，要用官方"载体封装"而不是"模块本体"来判**：模块底面小焊盘（BAT±/NFC/SWD/3V3/GND/VBUS）朝下贴在载体的**顶面**铜箔上，载体焊盘就该在顶层；14 个边焊盘是模块的沉金半孔，载体上是无孔 land（孔径 0 正确）。判据口径必须是**官方载体封装**（Seeed `New_XIAO_Series_Footprints/XIAO-nRF52840-SMD.kicad_mod`）+ 官方 Dimensioning.dxf，而不是模块本体尺寸。核对时把官方封装与库内封装做"镜像+平移"比对：镜像轴 = 模块长轴，编号→物理脚逐脚对应（XIAO nRF52840：pad1=D0 在 USB 端、D 列在 -x 侧、3V3/GND/VBUS 在 +x 侧）。

75. **官方机械资料包的 .xls 可能是空包**：`Bottom-pad-positioning.zip` 里的 "Bottom Key coordinates .xls" 实为 xlsx 包但**无 worksheet 部件**（只有 theme），zip 里能列出条目≠有表格数据。判据：先 `zipfile` 列出全部部件，确认有无 `xl/worksheets/*.xml`；没有就换官方 KiCad 载体封装 + Dimensioning.dxf 交叉验证，别把空表当"无数据"。

76. **EDA 旋转方向必须实测，别照抄库坐标**：本板实测 rot0=画布(lib_x,lib_y)、rot90=(-lib_y,lib_x)、rot180=(-lib_x,-lib_y)、rot270=(lib_y,-lib_x)；任务系 y 向下再翻转一次。探针做法：放一个 22 焊盘的大封装 → 读回 1 号与 12 号焊盘的绝对坐标 → 反解变换，一次就能定死 4 个朝向，避免后续全盘重排。

77. **"库内丝印 bbox"里可能混着非本体原语（天线/USB 舌片、位号文字）**：XIAO 库内封装的丝印并集比官方 21×17.8 本体大（多出天线禁铜舌片 1.5mm 与文字），直接当覆盖区会造出假缺陷。对策：按任务书口径把"模块本体轮廓"与"工艺禁布舌片"分开建模（本体进覆盖区、舌片进禁铜区），并在落位时让舌片外缘落在板内（XIAO 案例：原点 x 由 34.0 改 32.5）。

78. **0603 丝印必须按 ARC 段跳参数解析，否则虚增到 −2.29mm**：POLY 路径是 `x,y,"L",x,y,...` 与 `x,y,"ARC",cx,cy,r` 混排；把数组按 2 步长取对会在遇到 "ARC" 后错位配对。正确做法：遇字符串 "ARC" 跳 4 个元素、其余数字按序两两配对（弧的外扩不超过相邻直线极值，可跳过）。v1 曾因此把 0603 丝印算成 −2.29mm 造出假缺陷。

79. **`pcb_PrimitivePad.modify({net})` 写网后必须先保存再回读**：未保存时 `getAllPinsByPrimitiveId()` 读回的 net 仍是空串，但 `pcb_PrimitivePad.get(id).getState_Net()` 已显示新值 —— 两种回读口径不一致会误判"赋网失败"。正确序：modify → `pcb_Document.save()` → 等 8~10s → 再用 `getAllPinsByPrimitiveId` 全量对账（本次 70 条赋网 0 不符、16 网全等）。

80. **布局站"落位"不等于"位号归位"**：`pcb_PrimitiveComponent.create` 不会把位号文本移到器件中心（文本是独立的 L3 Attribute 原语，modify 组件不跟随）。v1 的 `pcb_layout_from_spec.py` 顺带**回中**，用 create/modify 直接落位时必须单独跑一次 `pcb_text_place.py`（新规则：归位到**自身丝印之外**，见 #18 与 #82），否则位号会**停在器件本体中心 = 落在自身丝印轮廓内**。（"文本×他件焊盘"是真重叠不是假重叠；只有 gap0.5 外扩环下的穿邻件才是噪声。）

81. **候选朝向的离线几何筛查应复用官方 `pcb_overlap_check.py`（合成 dump）**：自己用 bbox 并集复算覆盖区会与官方口径（游离原语剔除、ARC 解析、pad prot 自转角）不一致，筛出假冲突/漏真冲突。做法：按候选生成合成 dump（pads 用绝对 canvas 坐标 + prot + layer）→ 调官方检查器 --dump/--bodies --gap 0/0.5 → 以其 count 作为硬闸门。

## 位号文本归位类（2026-10-22 flexiheal-v2 G3T 实测新增）

82. **位号文本"回中"必落进自身丝印，且属性 (x,y) 是 em 盒【左下角】不是盒心**：`pcb_PrimitiveAttribute.get()` 的位号属性 `alignMode=3`，实测 (x,y) 是文本 **em 盒左下角**（rot=0 盒 = [X, X+W]×[Y, Y+H]，W=0.65×字号×字符数、H=字号；rot=90 盒 = [X-H, X]×[Y, Y+W]），**旧脚本按"锚点=盒心"建模会让判据整体偏移半个盒**（实测 U1 位号墨迹比盒心模型偏 +0.52/+0.61mm，判据"绿"而画布上压线）。同时"回中"= 器件本体中心 = **必然落在自己的丝印划定范围内**（flexiheal-v2 板 23/23 全中）；旧工具 `pcb_text_place.py` 搜索序首候选是"中"且 `hits_foreign()` 对 own 直接跳过，检查器 `pcb_overlap_check.py` 文本层也对 own `continue` ⇒ "文本压自身丝印"**在规则与实现两层都没有任何检查器会报**。对策：①位号归位判据必须含"文本盒 ∩ **自身**丝印轮廓 = ∅"与"不压任何焊盘"，搜索序**先排除自身丝印禁区**、**剔除「中」候选**；②锚点按左下角建模（实测标定见 `projects/flexiheal-v2/reports/_g3t_probe.json`）；③检查器必须新增 `文本×自身丝印范围`/`文本×自身焊盘` 条目，不得对 own 跳过；④库封装文档源可能漏采丝印原语（XIAO nRF52840 的 USB/天线舌片外缘真值 local y=487.499mil，库 dump 只有 428.503mil），**"自身丝印轮廓"要用原始封装 dump 或覆盖表**，否则位号会压在实际画出来的舌片上（画布实测舌片外缘 task x=21.28~21.51）；⑤丝印描边（实测宽 ~0.235mm）与文本描边（lineWidth 6mil）要在判据里各留 ~0.13/0.12mm 余量，否则"零重叠"是假的；⑥写完必须**截图量测复核**——`getCurrentRenderedAreaImage` 只在 **save 之后**才刷新（改属性不刷新，实测连续 3 次截图 MD5 相同）。

83. **"器件之间要保持适当间隙"是硬约束，且必须做双口径**（2026-10-21 委托方指示，flexiheal-v2 固化）：覆盖区 = **丝印层(L3) ∪ 焊盘** 联合包围盒（模块/连接器/按键的本体丝印都算）；**gap0 = 丝印范围零覆盖，硬指标 = 0 对**（不合格即整改，不许放行）；**gap0.5 = 覆盖区各边外扩 0.25mm 后不得互相覆盖、且不得零深度相切**（零深度相切 = 0 间隙 = 不合格，v1 曾容下两对）。处置顺序：先挪自由件 → 再挪派生小件（保去耦 ≤2mm 等硬约束）→ **不动**大件/连接器/板框；工艺冲突挪件自主执行并记日志，其余越界挪件停下上报。交付要附**逐对可复现证明**（253 对全量距离 + 最紧 8 对），不能只写结论。三个易错点：封装丝印的 **ARC 圆弧段**按圆弧解析不得按点对直连（v1 曾把 0603 丝印虚增到 −2.29mm 造出假缺陷）；**焊盘 prot 与 layer 必须进 dump**（v1 漏判 prot=90 的真实重叠）；**位号文本另立口径**（见 #18/#82：位号不得落在自身丝印轮廓内，且文本层不参与 gap0.5 外扩环噪声判定）。
84. **"换个朝向就好布"必须做成闸门，不能只写进约束条文**（2026-10-21 委托方指示，flexiheal-v2 固化）：器件朝向（rot/mirror）**不进 IR、不进网表、也不进丝印间隙判定**，却直接决定布不布得出来。流程 = **枚举有意义的朝向候选（旋转安全；镜像仅限无极性件，LED 阳/阴极不对称，镜像即电气错误）→ 代理评分（每网最小生成树总长、异网直线段交叉数、跨板惩罚、关键链总长与直线性、走廊占用率）→ 硬可达性闸门（离线 A* 或原生路由器对关键网做可达性预检，不可达即淘汰并记不可达网与阻塞源坐标）→ 择优落位（只重落差异件，其余零漂移）→ 重跑几何复检**。红线：**几何是硬门，评分只在合格布局之间排序**——flexiheal-v2 实测里评分最优的候选（MST −60%、交叉 −80%）因 5 对覆盖区冲突 + 2 焊盘出板被几何闸门淘汰，故维持基线朝向。**候选表与评分明细必须落盘**。另：**朝向闸门的可达性预检若用自研 A*，注意端点隔离陷阱**——焊盘自身按 间距 膨胀后会把起点/终点的相邻网格点全部封死，导致"起终点都在单板上却判不可达"的假阴性，必须先修端点膨胀再取结论。

## 布线/网表一致性类（2026-10-22 flexiheal-v2 G4 实测新增）

85. **`pcb_PrimitiveComponent` 的 `setState_OtherProperty()` 直写不会提交，读回值原封不动——必须走 `modify()`**：`setState_OtherProperty(o)` 返回 ok=23，但 `getState_OtherProperty()` 读回**完全没变**（异步对象缺 `done()/toSync()`）。对比 EDA 的 `modify()` 源码：`async modify(t,i){...;t.isAsync()||t.toAsync();...;l!==void 0&&t.setState_OtherProperty(l);t.isAsync()?t.done():t.toSync();t}`——**`modify()` 内部会补 `done()/toSync()` 提交**。flexiheal-v2 实测：G3 用 `pcb_PrimitiveComponent.create(dev,...)` 落的 23 件**从未携带原理图符号关联**（`Symbol`/`PCB Layer`/`Channel ID` 三个 props 全缺），导致 DRC 报 `Netlist Error | Import Changes`（"PCB and schematic netlist does not match"）——**网表本身逐叶比对 16 网/63 引脚/70 焊盘 0 差异**，错的只是元数据。对策：PCB 侧元件必须补齐原理图侧同名 props（`Symbol`=原理图符号 uuid、`PCB Layer`='Default'、`Channel ID`='$1In'），且**必须用 `modify()` 写**；写完 `close+open` 复位复测 DRC 才算数。

86. **`pcb_Drc.overwriteNetRules` 写的网级线宽规则不随文档 close+open 持久化，且不参与 DRC 判定**：写入后同会话回读 16/16 正确，**文档复位后全部回到 "default"**；`saveRuleConfiguration(cfg,name,true)` 返回 true 也不持久化。另实测把某网规则临时改到远超实际线宽（如 BAT+ 0.9mm）**DRC 仍报 0 错** ⇒ 规则被读取但不构成 DRC 判定项。结论：**"网级线宽规则已写入并回读确认"这一条可以在写入后立刻达成，但规则守不住线宽**，线宽合规必须靠**布线后逐段实测**兜底；任何"文档复位后"流程都要**重写**规则，别以为规则还在。

87. **新建图元后立刻测 DRC 会给出假绿灯——连接类结论必须 close+open 复位后再测**：G4 试放 3 颗**不接触任何铜面**的孤立 GND 过孔，**新建后立刻 DRC = 0**；**文档复位后再测 = 6 条 Connection Error**（孤立孔是断开的 GND 成员）。这与 pitfalls #41 同源（连接类数字虚高），但方向相反：**新建图元的"未连接"状态在复位后才被计入**。对策：任何"新放过孔/走线是否连通"的判定，**必须复位后再取 DRC**；在此之前"DRC=0"只能证明"没引入间距/物理类冲突"，不能证明"连通"。

88. **`getCurrentRenderedAreaImage()` 的截图只在 `save()` 之后刷新**：改完属性未保存时连续截图 MD5 相同（与 G3T #82 同源，此处复现于 G4 收尾出图）。出终态图前必须先 `pcb_Document.save()` + 复位 + 再取图，否则截到的是旧画布。

89. **原理图 vs PCB 网表的差异必须做"逐叶深度比对"，且要能区分"真网表差异"与"导出格式差异"**：`getNetlistFile()`（原理图侧）与 `pcb_Net.getNetlist()`（PCB 侧）两份 JSON 的**顶层键序不同**（sch 有 `altLibs`、pcb 有 `footprintAttrKeys`）、**引脚对象键集不同**（sch 多 `props:{Pin Number}`、pcb 少 `Symbol` 等）——直接 diff 会得到 279 处"差异"而其中**真网表差异为 0**。做法：先把两侧都归一到 `{net: [位号.引脚号]}` 再比成员（本次 16 网 / 63 引脚 / 70 焊盘全等）；再把残留叶级差异按 key 分类（`Symbol`/`PCB Layer`/`Channel ID`/`封装` 属**元数据**而非网表）。**只比"网名×成员"会漏掉元数据类错配**（G4 的 DRC 错误正是这一类），只比 JSON 又会被导出格式噪声淹没——两步都要做。

90. **EDA 返回的 Blob 类对象必须 `await blob.text()` 取内容，直接 `JSON.stringify(blob)` 得到 `"{}"`**：`sch_ManufactureData.getNetlistFile()` / `sys_FileSystem.readFileFromFileSystem(path)` 返回的都是 **File/Blob**，不是字符串；`JSON.stringify` 因 Blob 无可枚举属性而得 `{}`（曾据此误判"网表为空"）。同理 `drc` 结果、`pcb_Net.getNetlist()` 也要先确认类型再解析。**取网表/取文件/取图三处都踩过这个坑**。

91. **平面几何的"线段到焊盘矩形"距离不能用矩形对角线代替**：把 pad rect 当成一条从对角到对角的线段算距离，会在矩形外的区域**低估**距离 → 规划出实际压焊盘的落点（G4 的 BAT+ 末段穷举曾给出"过孔与 U1.19 焊盘重叠 0.28mm"的假优解）。正确口径：矩形到线段距离 = 四个角到线段的最小距离，**且线段端点落在矩形内时必须判 0**（相交）。同理，**通孔(24/12mil)落点要同时判"对异网铜的净距"与"对通孔焊盘的孔-孔净距"**——通孔贯穿所有层，对**顶层 SMD 焊盘**同样要让距。

92. **"板边间距 ≤10mm"这类约束要先确认它约束的是"到板边的距离"还是"相邻孔的间距"**：两种口径结论差别巨大。本例中前者（缝合孔距板边 ≤10mm）实测 14/16 满足；后者（相邻缝合孔沿板周 ≤10mm）因**天线禁铜区占了板右缘 20.5mm**而物理上不可能，且**该段不能补孔**（补了会违反禁铜约束）。遇到"两条约束互相打架"时，**按链路短优先 + 记档待裁决**处理（见 D056），不要为了满足一条约束去违反另一条硬约束。

93. **离线 A* 的"同网可重叠 + 0.05mm 余量"对 EDA 的 0.2mm 净距规则过于乐观，算出的"最优路径"铺上去会被 DRC 判违例**：flexiheal-v2 实测，按真实净距模型（SMD 只挡同层 / 通孔挡两层 / 净距 0.2mm / 禁 via-in-pad）算得 BAT+ 主干最短 21.896mm（比现网短 2.4mm），**真铺上去后 DRC 报 4 Clearance（撞 BTN1_N / BAT_SENSE / U1.20-GND）+ 2 Connection**。根因：①"同网铜面可重叠"在**焊盘对焊盘的净距**上不成立（U1.19 与 U1.20 间距仅 1.9mm，中间没有走线通道）；②0.05mm 余量远小于 0.2mm 规则，斜向接近时必然擦边。对策：①**A* 结果只能当"方向性参考"，落地前必须用 ≥0.2mm 的净距 + 逐段 pad-层别判定复算**；②落地后**必须 DRC 复测**，不通过即按备份精确回滚（清空走线/过孔后逐条重建，不要"删掉画错的段"——会残留坐标漂移）；③DRC 的 Clearance 违例条目里的 `obj1/obj2.suffix`（如 `U1_20`、`e184`）是**定位违例的最快入口**（`e184`=走线图元 id，`U1_20`=焊盘），可据此直接反查是哪条线。

94. **回滚画布要用"清空 + 按备份逐条重建"，不要"删掉错的段"**：批量删除再逐条重建会让 EDA **自动合并共线段**（实测 211 段 → 209 段，铜长与几何完全等价，DRC=0），肉眼会以为"丢了走线"。判定方法：**按无向键（端点排序后）做多重集差分**，而不是按有向端点比对——有向比对会把"端点顺序翻转"的同一段误报成"增 1 删 1"。真正的丢失要看**每网的段数**与**铜长**（`netLen`）是否变化。

## 覆铜/铜皮条类（2026-10-22 flexiheal-v2 G5 站实测新增）

95. **EDA 覆铜填充引擎只认 `'R'` 矩形多边形；自由 `'L'` 点列多边形「能建不能填」**：用 `pcb_MathPolygon.createPolygon([x0,y0,'L',x1,y1,...])` 创建的覆铜，`pcb_PrimitivePour.create()` 返回成功、状态里多边形也回读得到，但 **`getCopperRegion()` 恒为 null、画布不渲染、`pcb_PrimitivePoured` 不产生图元**（= 填充结果为空）。只有 `['R', X, Y, W, H, 0, 0]`（**末尾必须带两个 0，缺一即 `createPolygon` 返回 falsy**）能被填充引擎处理。v1 的 capsule 早已写过「多边形 API 仅支持 R/CIRCLE」，此处补上**可复现的判据**与尾部 `0,0` 的坑。

96. **覆铜的填充几何有三个读数坑，全部会毁掉几何量测**：① **填充多边形坐标单位 = 10 mil（0.01 inch），不是画布 mil**——判据是填充外框比铺铜轮廓内缩 `lineWidth/2`（0.1mm）：轮廓 `R(19.685,1161.315,1732.63,1141.63)` → 填充外框 `x∈[0.6016,44.4072] mm`（若按 mil 直接读会得到「铜面只有 4.4mm 宽」的错觉）；② 数组编码 `[x0,y0,('L' x,y…)|('ARC' sweep, x_end, y_end)…]`，**`'ARC'` 后的 3 个数是 (扫角, 终点 x, 终点 y)，端点取第 2、3 个数**（取第 1、2 个会把扫角当坐标）；③ `sweep≈180` 的 `'ARC'` 是半圆，必须按 `(起点+终点)/2` 为圆心补弧点，否则整圆退化成一条线（用 EDA 的 `pcb_MathPolygon.calculateBBoxWidth/Height` 对每条子多边形做自校验最省事）。

97. **覆铜引擎不做「区 vs 区」互斥裁剪 → 不同网的两张板级铺铜重叠 = 直接短路**：`pcb_PrimitivePour.create()` 的填充只按**硬铜**（走线/焊盘/过孔）做间隙让位，**不把其它覆铜的填充当障碍**。实测：板级 GND 铺铜 + 板级 BAT+ 铺铜 → 两者填充直接重叠 → DRC 报 `Clearance Error | Copper Region(Filled) to Copper Region(Filled)`（= 真实短路）。解法：**把 GND 铺铜按 guillotine 切块**，让出同网铜皮条的占位矩形（块间多边形要互相**重叠 ≥0.2mm**，否则铜面之间会留缝，见 #98）。

98. **铺铜填充会从自身多边形边界内缩 `lineWidth/2`（默认 0.2mm → 各边 0.1mm）**：① 相邻两张铺铜的多边形若只是**相切**，其填充之间会留下 **0.2mm 铜缝** → GND 网被切断 → 缝合孔变孤岛 → DRC Connection Error；② 铺铜填充与异网铜皮条之间的净距要比「多边形间距」再减去两个 0.1mm 才是真实铜面净距 ⇒ 切块时的孔洞要**外扩 ≥0.5mm** 才能保住 0.3mm 铺铜间隙。

99. **覆铜填充对异网硬铜的让位净距 = 规则 0.254mm **再**内缩 lineWidth/2=0.1mm ⇒ 真实「死区」0.354mm，铜皮条补线宽的天花板要按它算**：flexiheal-v2 实测（VDD 段 46209efdd9487f27）：同侧 LED_A_DRV 走线边缘距中心线 0.555mm，覆铜铜面只到中心线上方 **0.1964mm**（= 0.555 − 0.254 − 0.1）⇒ 与走线合并后的联合宽 0.325mm，比理论上限低 0.03mm（受焊盘侧 0.1302mm 限）。**推论：想补线宽时，「同网铜皮条」比「加宽走线」差一个净距档** —— 覆铜只能进到 0.254mm 净距（再内缩 0.1），走线能进到 0.2mm 净距规则；若两侧极限不对称（例：上 0.355 / 下 0.1302），**对称加宽不可行、必须改用「同网并行走线」把非对称余量用满**（并行走线上缘压到 0.2 净距、下缘与原段重叠即可连续）。另：项目里写的「铺铜间隙 0.3mm」在 1oz 规则表里是 **Copper/Plane Zone↔Track=0.3**，而 `pcb_PrimitivePour.create(...,'solid',...)` 建的实心覆铜在 DRC 表里按 **Fill Region↔Track=0.254** 判，且填充还要再内缩 0.1mm ⇒ **不要用 0.3 去算铜皮条能贴多近**。

100. **EDA 的 `Netlist Error | Import Changes` 比较**含元件的封装属性**（footprint attribute）—— 只改 PCB 侧的自定义属性就会触发网表错**：flexiheal-v2 实测（G5F）：把 PCB 侧 J4 的 `otherProperty['选配']` 从「DNP-不贴…」改成「贴装（…）」后，复位复测 DRC = **1 条 Netlist Error**（"PCB and schematic netlist does not match"）；**A/B 对照**：改回原值 → 复位复测 DRC = 0 ⇒ 错误只来自该属性，且**与几何改动无关**。对比：`eda.pcb_Net.getNetlist()` 的两侧 props 里另有 47 处差异（PCB Layer Default/TopLayer、Symbol、SymbolName、封装 等）**并不触发 DRC** —— 规律是「**两侧同名且都存在、但值不同**」才报错，「一侧缺失」不报。对策：① 改 PCB 侧封装属性必须**同步改原理图侧同名属性**；② 改完用 `eda.pcb_Net.getNetlist()`（PCB 侧）与 `eda.sch_ManufactureData.getNetlistFile()`（原理图侧）逐 props 对比确认；③ **原理图页在同一 session 里可能根本打不开**：`eda.dmt_EditorControl.openDocument(pageUuid)` 会返回页签名但**不生成页签**，`getDocumentReadyState(tabId)` 恒为 `notLoaded`，而 `eda.sch_PrimitiveComponent.getAllPrimitiveId()` 抛「获取所有器件的图元ID失败」——此时只能另开会话（另一窗口）改原理图，或**直接改工程 zip 里的 .epru**（.epru 是逐行 JSON 记录：原理图侧属性是 `{"type":"ATTR",...,"value":"...","key":"选配","parentId":"<元件id>"}` 独立行，PCB 侧则是 `{"type":"COMPONENT",...,"attrs":{...}}` 行内 JSON ⇒ 可按 `parentId`/`COMPONENT id` 精确定位后单点替换；改前务必备份 zip）。④ 另：**同一工程被多个 EDA 窗口打开时，各窗口的内存态互不同步、且 `pcb_Document.save()` 不会写工程 zip** ⇒ 判定"改没改成"必须回读，不能凭返回值。

101. **同一工程里的同一张原理图页只能被一个 EDA 会话持有**（2026-10-22 flexiheal-v2 实测）：会话 A 打开 `P1.Schematic1` 后，其它会话 `eda.dmt_EditorControl.openDocument(pageUuid)` **会返回 tabId 但不生成页签**，随后 `eda.sch_Drc.check` 报 `SCH_DrcUtil: doctype(3) not support check sch drc`（doctype 3 = PCB）→ 表现为"原理图打不开"。**定位方法**：`lceda-pro session list` 列出所有会话，逐个 `getSplitScreenTree()` 找到 `title=="P1.Schematic1"` 的那个会话，在**它里面**操作。**另注**：`activateDocument` 在**同一次 invoke 内不会改变 doctype 上下文** → 激活原理图页必须**分两次调用**（先激活、再查 ERC/读图元）；`lceda-pro` 还有 `session/ list|close|abort` 可用来清理多余会话。

102. **在原理图上做"可行性实验"= 制造设计缺陷**（2026-10-22 事故，主 session 自造）：为验证"原位重放器件、走线是否自动重绑"，在原理图上删了 R1 又没还原 → 原理图少一个器件 → PCB 侧立刻出现 `Netlist Error | Import Changes`（且这类错误**在 PCB 侧报错、根因却在原理图**，极易误判为几何问题）。**纪律**：① 任何原理图改动前先**导出网表留基准**并确认"哪个会话持有该文档"；② 改完必须三连验证：**导出网表与基准逐网比对 → 复跑 ERC → 复测 PCB 侧 DRC**，三者全绿才算改完；③ 实验只在**备用页/副本**上做（v2 的 `P1.Schematic2` 就是为此存在）；④ 删器件前先记下它的**全部属性快照**（Designator/Name/Unique ID/Channel ID/Device/Symbol/Footprint/位置/角度）——`sch_PrimitiveComponent.delete` 会连同它的连接一起带走，而 `sch_PrimitiveAttribute` **没有 create 接口**（只能 get/modify/delete），所以"删了再照原样造回来"只能靠属性原语逐条 modify。

103. **本地文件夹工程：编辑器 save 不写工程归档 zip**（2026-10-22 flexiheal-v2 实测）：工程是文件夹工程（`E:\电路设计lexiheal-v2lexiheal-v2_<时间戳>.zip`），`pcb_Document.save()` / `sch_Document.save()` / `closeDocument` **都不产生新 zip**，磁盘归档会**落后于编辑器内存态**（实测落后一整站的工作量）。**后果**：从磁盘 zip 恢复会丢改动；从 zip 直接改（改 .epru 文本）能改但要自己保证与内存态一致。**对策**：① 交付前**以活体会话导出制造文件**（Gerber/钻孔/坐标/BOM），并在交付说明里写明"磁盘工程归档需在 EDA 里手动'保存工程'一次"；② 动 .epru 之前先备份 zip、保留可回滚路径；③ 任何站若把关键成果只留在内存态，必须在 capsule 里显式写"内存态未落盘"。

104. **不要用 `lceda-pro open --path` 打开本地工程文件；API 查名字要用官方参考命令**（2026-10-22 flexiheal-v2 实测）：① `lceda-pro open --path <.eprj/.eprj2/.eprj3/.elib>` 在**全在线模式**下会弹**模态框**"客户端全在线模式下不支持打开 .eprj 和 .epj2 工程文件，请在离线模式或全离线模式打开"——**模态框会卡住 GUI 渲染进程，该会话所有 invoke 全部挂起/超时**，看起来像"agent 死了"；而且它打开的是**另一份副本**，与活体会话脱节。**正确姿势**：只用官方 API `eda.dmt_Project.openProject(uuid)` → `eda.dmt_EditorControl.openDocument(pageUuid)` → `activateDocument`；真要开本地工程文件，先由人工切离线模式。② **别靠猜 API 名字**：`lceda-pro doc api` 直接给**类名 + 全部方法 + 参数签名 + 官方示例**（`--class-name X` 收窄到类，`--method-name Y` 看单个方法）。实测：靠猜把 `eda.pcb_ManufactureData` 试了半天才摸到导出方法，一条 `doc api --class-name PCB_ManufactureData` 就把 `getGerberFile`（默认等价"一键导出"：MM + 4:5 + 板内实际使用的层 + 钻孔图层）、`getPickAndPlaceFile`、`getBomFile`、`getIpcD356AFile`、`getIpc2581CFile`、`getNetlistFile`、`get3DFile`（**只有 STEP 导入的元件模型才会出现在 3D 导出里**）、`getManufactureData`（**仅私有化部署有效，其它版本直接 throw**）连同参数全列了出来。③ **会话卡死的识别与处置**：模态框 = 渲染进程被占住 → `lceda-pro session list` 找到该 session → `lceda-pro session close`（必要时 `session abort`）→ 换会话继续，**不要在同一会话上重试**。

105. **讲义附图必须"晚于工程最后修改"，且原理图图必须用官方导出 API 重导**（2026-10-22 flexiheal-v2 G7 实测）：G7 站做讲义素材时，`figures/` 里混进了**三张 10-21 22:52 的旧原理图**（`sch_full` / `sch_buttons` / `sch_mcu`），而它们早于 **J1 换 2 脚件（D042）**与**原理图 R1 还原（D072）** → 图上还是旧的 4 脚 J1、R1 缺席。**"图上画的是不是这块板"必须按时间戳核，不能按文件名核**（同名的图在不同时刻内容不同）。**正确取图路径**：原理图 = **`eda.sch_ManufactureData.getPngFile(fileName?, resolution?)`**（官方 API，支持长边最大 4096 的高清导出；只传 width/height 之一按原比例拉伸）；PCB = **渲染 API** `getCurrentRenderedAreaImage`（**注意 pitfalls #29/#71：只回当前视口合成图、需独占窗口、需等画布重绘**），因为 `PCB_ManufactureData` **没有 getPngFile**。**讲义交付前逐张核**：文件非空 + 时间戳 > 工程最后一次修改 + PROVENANCE 表登记"来源 API + 导出时刻 + 展示的工程状态 + 对应 decisions 编号"；**布局期截图与终态截图并排使用时，图注必须写明"布局阶段（铺铜前）/ 终态（铺铜后）"**。

106. **自造的"不贴装"标记会让 EDA 的 BOM 过滤器静默失效**（2026-10-22 flexiheal-v2 实测）：用**自定义属性**（如 `otherProperty['选配']`）标注 DNP，落到 `pcb_ManufactureData.getBomFile(filterOptions)` 上就**过滤不出"仅贴装 BOM"**——实测 `filterOptions` 命中 0 行（只有表头），而全量 BOM 正常。**根因不是过滤语法，是标记三态不统一**：7 件 = `"DNP-不贴（选配件默认不贴，C12）"`、1 件 = `"贴装（…）"`、**其余 15 件根本没有这个键**（落位时只给选配件写了）→ 任何"按该字段过滤"的条件都凑不出预期行数。**对策**：① **贴装 BOM 不要依赖 EDA 的属性过滤**，而是从全量 BOM **按 DNP 位号清单剔除**（清单以 `spec/bom.json` 的 `populate` 字段为唯一事实源），并让剔除的行可逐行核对（剔除 7 行、剩 16 行）；② 落位时**给全部器件都写同一个 DNP 标记键**（要么都写 `选配`，要么都用 EDA 标准属性如 `Add into PCB`），杜绝"三态"；③ 标记改动（DNP ↔ 贴装）后**必须同步四处**：IR 器件条目与 `meta` 计数、PCB 器件 `otherProperty`、原理图器件显示名/备注、装配 BOM 与采购清单——漏一处就会在下游产物里表现为"行数对不上/贴了不该贴的件"。

107. **器件级属性在原理图与 PCB 两侧必须"成对"同步，否则 EDA 报 Netlist Error（看起来像几何问题）**（2026-10-22 flexiheal-v2 实测）：把某个器件的 `name`（值/Comment）或 `备注` 只在**原理图侧**改了（PCB 侧没改），**冷启动 DRC 立刻报 1 条 `Netlist Error | Import Changes`**——而 EDA 的 Import Changes 比对**不只**看 Unique ID / Channel ID / Symbol / 选配属性，**器件名与备注也参与比对**。这类错误的迷惑性在于：DRC 面板只给一个没有位置的 `Netlist Error` 条目，既不像 Clearance 那样能定位到几何冲突，也不像 netlist 断言那样能指向具体网络，**极易被误判为"布线/铺铜几何问题"而白查一轮**。**对策**：① 改任何器件级属性，**原理图侧与 PCB 侧同一次改完**，改完**逐字比对两侧同器件的 `name` / `备注` / `选配` / `封装` 四个字段**；② 改完**必须复位（close+open）后复测 DRC**——不复位可能读到旧模型而误判为"没问题"（本项目就出现过：12:12 复位是 0 错，改完原理图后 13:14 复位变 1 错）；③ 顺带检查**导出产物是否含该属性**（BOM 的 Comment 列、3D/IPC 文件里的器件名）→ 含则**重新导出**，否则交付物里留着旧名。

108. **工作流 DSL（`phase()` / `agent()`）不是普通脚本，落盘即失效**（2026-10-22 flexiheal-v2 G6 实测）：把"独立对抗评审"写成 `scripts/g6/adversarial_review.ts`，里面用 `phase(...)` / `agent(...)` —— 这是**工作流运行环境提供的门面函数**（CreateWorkflow / EvalWorkflowSnippet 的专用沙箱），**用 node / python 直接跑没有这些函数**，脚本落盘 = 静默失效；实测 `reports/` 下**只有任务书与那个 .ts、没有任何评审结论产物**，即该项**实际从未执行**，而它在判据里是必过项。**对策**：① 需要"独立上下文只读复核"时，**派一个只读子 agent**（把任务书原文给它 + 写死终止条件 + 指定结论落盘路径），**不要**自己写 DSL 脚本；② 若确要用工作流，**必须通过 workflow 工具正式提交**，不能靠落盘执行；③ **凡是以"文件已生成"自称完成的项，验收时一律查"结论产物是否存在且非空"**——任务书/脚本/日志行数都不是证据；④ 工作流规格类文件请放到 `reports/` 并在文件名或头部注明"**工作流 DSL，非可执行脚本**"，别留在 `scripts/` 里冒充可执行脚本。

109. **官方复核器 `gerber_review.py` 有两处会让嘉立创导出的 Gerber 全部误报 FAIL 的缺陷（2026-10-22 flexiheal-v2 G6 实测并修复）**：① **光圈选择只认独立一行 `D10*`，不认嘉立创的合并写法 `G54D10*`** ⇒ `cur` 恒为 `None` ⇒ 第一个 D03 即报「用了未定义光圈（孤儿 D 码）」。**实测孤儿 D 码实为 0**（GTL 用到的 D10..D38 全部在 `%ADD` 中定义）。② **`classify()` 用「文件名含 drill」判 Excellon** ⇒ 把 `Gerber_DrillDrawingLayer.GDD`（Gerber 钻孔示意图）当成钻孔文件⇒ 报「缺 METRIC/INCH 单位头」。**对策**：**先按 Gerber 后缀/前缀（`.g*` / `gerber*`）判定，再轮关键字**；光圈选择**同时认 `D\d+` 与 `G\d+D\d+` 两种写法**。**通用教训（更值钱的一条）**：**两条独立路径算出不同数字时，先查工具、再查口径、最后才查设计** —— 本次 6 条 FAIL 全是工具缺陷，另有一次「draw 2066 vs 698」是**口径差**（官方把 G36/G37 区域填充轮廓段计入 draw）而非数据差。改工具前务必备份原版。｜证据: plugins/pcb-wish/scripts/verify/gerber_review.py（修复前 .g6bak）、projects/flexiheal-v2/reports/_g6_gerber_review.json

110. **`pcb_ManufactureData.getBomFile` 的 `filterOptions` 用板上不存在的属性会静默返回 0 行（2026-10-22 flexiheal-v2 G6 实测）**：官方文档示例 `{property:'Add into BOM', includeValue:'yes'}` 在本板**返回 0 行**（PCB 侧组件根本没有这个键）；板上只有 7 件 DNP + J4 有自定义 `选配` 键，其余 15 件连 `选配` 都没有。**可用口径**（实测 11 行 / 16 器件）：`[{property:'选配', includeValue:'DNP-不贴（选配件默认不贴，C12）'}]`。**语义实测**：匹配到该值 ⇒ **剔除**该值的器件；不存在的值 ⇒ 不过滤；给两个值 ⇒ 两个都剔。**对策**：过滤条件必须用**板上真实存在的属性键**，且**每次过滤后必须回读行数与位号集合**（0 行是最容易被当成「导出成功」而漏掉的失败模式）；「不贴件」的事实源是 `spec/bom.json` 的 `populate` 字段，不是 EDA 属性。｜证据: projects/flexiheal-v2/reports/_g6_consistency.json、export/assembly/…BOM_populated.csv

111. **「独立对抗评审」的执行通道要在开工前先探通，别把工作流 DSL 当普通脚本落盘（2026-10-22 flexiheal-v2 G6 实测踩坑）**：① `phase()` / `agent()` / `artifact.*` 是**工作流运行环境（CreateWorkflow / EvalWorkflowSnippet）**的面门函数，**用 node / python 直接执行时这些函数根本不存在** ⇒ 脚本落盘即静默失效（不报错、不产出）。② 若该环境**不提供工作流执行能力**（子 agent 会话里 `CreateWorkflow` 只能编译、不能跑），改调外部 CLI（如 `claude -p`）也可能因**额度/鉴权**直接失败（实测 `403 用户额度不足`）。**对策（开工前 5 分钟探通）**：① 先确认执行通道可用（跑一个只读最小用例）；② **派评审员的判据写成「结论必须落盘到某个文件」**，这样「有没有真跑」一眼可查——**只有一行报错的那个文件不是评审产物**；③ 通道不可用时**如实写「未执行」**，绝不写成 PASS，并由能执行的主 session 派；④ **别把死路脚本留在工程目录里**（会误导后来人），要么删、要么在文件头注明「工作流 DSL，非可执行脚本」。｜证据: projects/flexiheal-v2/reports/_g6_adversarial_review.md、_g6_adversarial_review_brief.md


112. **"禁铜区铜面积 = 0"这类判据必须先定义"谁的铜"**（2026-10-22 flexiheal-v2 对抗评审发现）：模块类载体的禁铜区里**天然含有模块自身的 land**（XIAO 的 U1.7/8/21/22 四个焊盘共 9.41 mm² 落在天线禁铜带内），所以"区内铜面积 = 0"**字面永远不成立**，写进判据就会逼着执行者给出一个"测不出来=0"的假结论。**正确写法**：**区内载板新增铜（铺铜+走线+新焊盘）= 0；模块自身 land 豁免**，并在证据里把两个数**分列**（新增铜 / 模块 land）。配套的量测方法要能区分二者——按焊盘/走线/铺铜**分类**统计，而不是笼统求和。
113. **栅格化工具的 Y 翻转会把"矩形/圆盘"静默变成空操作**（2026-10-22 flexiheal-v2 实测）：Gerber 画布 Y 向上、任务系 Y 向下 ⇒ 栅格行号 `gy()` 随 gerber_y **递减**。若沿用"y 小→行号小"的直觉写成 `j0=gy(min)/j1=gy(max)`，则恒得 `j0>j1`，被 `if j1>=j0` 守卫**整段跳过** ⇒ **矩形与圆盘一格都不填，而多边形（因 min/max 用反了）却正常**。后果极隐蔽：**禁铜区量出来是 0.0 mm²（"完美通过"），因为它只测到了多边形铺铜，测不到焊盘与走线**——结论方向对、证据完全无效。**对策**：① 栅格化入口**统一把 y 翻到任务系再落格**，不要在每个图元里各自换算；② 任何"光栅法测面积 = 0"的结论，**必须用一个已知存在的图元做自检**（例：拿一个完整包住某焊盘的矩形去跑，area 必须 > 0）——**"工具先自校准，再看结果"**；③ 同一判据用**两条独立代码路径**跑（本次：官方复核器 + 自写解析器），且**先统一口径**（官方把 G36 轮廓段计入 draw、自写只算走线描边，口径差不是数据差）。
114. **官方复核脚本会因厂商方言漏判**（2026-10-22 flexiheal-v2 实测）：嘉立创 EDA 导出的 Gerber 有三处方言差异会让通用解析器**漏报或误报**：① 光圈定义为 **合并写法 `G54D10*`**（只认独立 `D10*` ⇒ `cur` 恒 None ⇒ 首个 D03 被误判"孤儿 D 码"）；② **AM 宏光圈**（`%ADD10IconRect*%`，无逗号参数段）与 **RoundRect** 形状不在常见 `C|R|O` 内 ⇒ 光圈计数偏少（GTS 6/18、GBS 5/10、GDD 3/8）；③ 同一孔**同时写进 Through 与 Via 两个钻孔文件** ⇒ 孔数累加重复（114 vs 真值 **64**）。**对策**：修官方脚本而不是绕开它（本项目已修三处并保留 `.bak`），修完**用独立解析器逐项对齐**（光圈数/孔数/flash/draw/板框）。
115. **"证据通过"不等于"方法有效"**（2026-10-22 flexiheal-v2 对抗评审）：一条判据算出 0/全 PASS，也可能是因为**量测方法测不到那类图元**（见 #113）。**验收时对每个"零结果"判据追问三句**：① 量测函数是否真的覆盖了目标图元类型（矩形/圆盘/多边形/走线/焊盘各测了没）？② 有没有用已知存在的对象做**自检**？③ 结论与另一条独立路径**是否一致**？三问不过，即使数字漂亮也要判"证据不成立"并重做。
116. **挪过孔/改走线时，空铺铜块会被一颗新过孔"复活"**（2026-10-22 flexiheal-v2 G4R 实测）：铺铜块因**孤岛被 EDA 剔除**而没有填充（本板 20 块铜皮里 `GND_L2_2` 是唯一无填充块）——把一颗**同网**过孔放进它的轮廓内，孤岛就被接上、该块**自动填充**（实测 +15.23 mm² 的 L2 铜），造成一次"没人要求的铜层变化"。**判据与做法**：① 每次重铺前先记录"**当前无填充的铺铜块清单 + 其轮廓**"；② 选新过孔位时**额外加一条约束：新过孔铜盘不得与任何"当前为空块"的轮廓相交**（实测本次把 C4.2 的过孔从 (32.6,2.5934) 挪到 (31.9,1.5) 就是为此）；③ 重铺后复核"**有填充块数**"与"**逐块铜面积**"与基线差 = 0（本次 20 块 / 19 块有填充、逐块差 0.0000 mm²）。**"铺铜块数"不等于"有铜的块数"**——只看块数会漏掉这一类变化。
117. **判"过孔在焊盘里"必须按焊盘的 prot（自转角）旋出真实外接矩形**（2026-10-22 flexiheal-v2 G4R 实测）：同一块板用**不旋 prot** 的朴素包围盒去判，会得到 **4 处**假/真混合命中——U1 的 RECT 焊盘 `prot=±90`（w/h 需互换）会把紧邻的 U1.1 误判成 via-in-pad，而真正要保留的 U1.18（Ø1.6993 mm 椭圆焊盘）反而只有用**包围盒**口径才命中。**权威口径 = 项目官方 `pcb_bodies_lib._pad_half_sizes_mil`**（本地 w/h 按 prot 旋出轴对齐外接矩形）：按此口径全板 **3 处**（C1.2 / C4.2 / U1.18），与任务书一致。**教训**：写"扫描类"判据前，**先确认几何口径与项目既有官方实现一致**，否则会同时出现假阴与假阳。
118. **一颗"地脚过孔"往往同时是三样东西**（2026-10-22 flexiheal-v2 G4R 实测）：C1.2 / C4.2 的过孔**同时**是 ①手焊焊盘的吸锡风险点 ②**板边缝合孔**（进 `spec/stitching_vias.json` 的 30 颗清单，影响 chord/arc ≤10mm 双口径）③**GND 铺铜与走线的连接点**。挪它之前**三样都要查**：查 stitching 清单（孔数与间距会不会破）、查铺铜块（会不会激活空块，见 #116）、查走线拓扑（谁靠它接平面）。本次三样都核过：缝合孔仍 30 颗且间距仍 ≤10mm、铺铜 20/19 与逐块面积零变化、连通 16/16。
119. **判"过孔压焊盘"不能用"圆心落在焊盘内"，必须判铜面是否相交**（2026-10-22 flexiheal-v2 G4R 收口实测，**修正 #117**）：#117 的"圆心落矩形内"口径在**两个方向同时出错**——① **漏判**：R17.2 (GND) 过孔圆心在焊盘矩形**外** 0.0218mm，但过孔铜盘（Ø0.6096）**压进焊盘 0.283mm**（copper_gap = **-0.283**），是真缺陷却被判为"命中 0"；② **误判**：U1.18 是 Ø1.6993mm **椭圆**焊盘，圆心距焊盘中心 1.1722mm > 半径 0.84965mm，**圆心根本不在焊盘内**，用外接矩形判却"命中"，害我一度把它当成必须保留的 via-in-pad 并开豁免。**权威判据 = 过孔铜盘圆盘与焊盘铜面区域是否相交**（`dist(过孔圆心, 焊盘形状) - 过孔铜盘半径 < 0`），焊盘形状按 **prot 旋出的矩形 / 椭圆的等效半径**精确求距离。**配套**：判"贴边"要用**孔边**（孔壁）而非铜边——同网焊盘的吸锡风险来自焊锡进**孔**，所以净距 = 焊盘边缘到**孔壁**的距离。**自查**：几何算法必须用独立方法交叉验证（旋转矩形四角 + 点在凸多边形内判定 + 点到线段距离），本项目全板 3500 对样本最大偏差 **0.000000000 mm**；同时**注意"同网不报 DRC"** —— 同网 via↔pad 间距没有 DRC 规则覆盖，**不体检就永远发现不了**。
120. **"过孔不要紧挨焊盘"是独立于 via-in-pad 的一条硬要求**（2026-10-22 flexiheal-v2 用户指示）：用户指出"过孔最好也不要紧挨着焊盘旁边"——**理由是手焊吸锡**：焊锡沿铜面被吸进过孔孔洞，焊点发虚、虚焊；且过孔铜盘挤占焊盘可上锡面积（0603 尤其致命）。这与"via-in-pad"是**两个不同判据**：前者是**距离**（同网孔边 ≥ 0.35mm，0.5mm 推荐档），后者是**相交**（0 容忍）。**取舍纪律**：阈值按**几何可达值**定——**允许个别点放宽到 0.35mm，也不得为了凑 0.5mm 去动已经验收过的器件位置/布线**。整改手段按代价排序：① **同轴外推**（沿走线方向把过孔往外推，走线段随之延长，拓扑不变、不动他网、不动器件）；② **自由搬迁**（仅限无主干的缝合孔，且搬迁后须仍在同网铺铜内，且**不得与任何"当前空铺铜块"的轮廓相交**，见 #116）；③ 都不可行 → **写豁免**（记录实测可达值 + 阻挡原因：哪个焊盘/走线在什么方向挡路），**不硬改**。本项目实测 20 个违规过孔中 **16 个可解**（同轴外推 9 / 搬迁 7），**4 个几何不可达**（模块 U1.4 逃逸孔向外即撞 U1.19 焊盘等）。
121. **"挪过孔"方案的净距模型，漏扣过孔铜盘半径 / 漏查同网孔洞，就会造出新的 DRC 违例**（2026-10-22 flexiheal-v2 G4R 实测，**主 session 自己的求解器出的错**）：按"过孔↔焊盘"新判据批量外推 14 颗过孔，**焊盘判据全过、但 DRC 从 0 变成 3 条**，根因是求解器模型有两处漏洞：① **走线↔过孔净距漏扣过孔铜盘半径**——过孔是**通孔**（穿透所有层），与走线/焊盘的距离必须扣掉 `VIA_R`（0.3048mm），只扣走线半宽会**系统性高估 0.3048mm**；实测 SWDIO 孔外推 0.2mm 后与 SWCLK 的 L2 走线只剩 **0.0635mm**（要求 0.2mm）→ `Track to Via` 违例 ×2。② **同网过孔之间只查铜面、不查孔洞**——同网铜面可以重叠，但**孔洞是钻出来的空腔**，两孔重叠就是 `Hole to Hole` 违例（DRC 按孔判，不按铜面判）；实测把 R17.2 的孔搬到 (14.018,28.924) 时，0.1884mm 处已有一颗同网 GND 孔 → 孔边距 **-0.1164mm**。**对策**：① 净距模型必须**逐项区分"铜面间距"与"孔壁间距"**（孔壁间距对**同异网一律查**）；② 求解器的每一次落点评估都必须是**完整 DRC 口径的子集**（走线/焊盘/过孔/孔洞/板边/铺铜），少一项就会漏；③ **求解器自己也要被验收**——方案生成后**必须冷重启跑一次真实 DRC**（本次就是 DRC 抓到的，不是自查抓到的），并且**主 session 要独立复跑 DRC，不采信子 session 的"应该没问题"**；④ 判"过孔之间"时**同网也要查孔洞**。
119. **"走线走廊净距"检查必须扣掉过孔自身的铜盘半径**（2026-10-22 flexiheal-v2 G4R 实测）：选点脚本若只按
   `线段中心距 − 对方走线半宽 ≥ 0.2` 判断，会给出**与异网走线铜面只剩 0.065 mm** 的"合格"落点
   ⇒ DRC 立刻报 `Track to Via`（实测 SWDIO 逃逸孔撞 SWCLK 的 L2 竖线）。
   **正确口径**：`线段中心距 − 对方走线半宽 − 过孔铜盘半径(0.3048) ≥ 0.2`。
   同一脚本还**必须查孔-孔间距**（过孔的孔壁 ↔ 他孔孔壁 ≥0.2997，板级 hole2Hole 规则）——
   本次方案把 R17.2 缝合孔放到另一颗缝合孔正上方 0.188 mm 处 ⇒ DRC 报 `Hole to Hole`。
   **教训**：**"选点方案"脚本不是免检环节**：它的每一条检查项都要与 DRC 规则逐条对齐，
   少算一个半径/间距就会在 DRC 阶段现形。
120. **EDA 改走线端点会「连带重连整条走线链」**（2026-10-22 flexiheal-v2 G4R 实测，`pcb_PrimitiveLine.modify`）：
   改一段的端点后，EDA 会把**该段所在的整条折线链拆开重画**（实测一段 BAT_SENSE 触发了 6 段重建、
   凭空多出 9 段），**段 id 全部变化** ⇒ 逐条 modify + 按 id 回读必然失败，还会把几何改歪。
   **可行做法（已验证收敛）**：① 先**算出目标线集**（内容口径：net/layer/端点/线宽）；
   ② 迭代「取活体态 → 与目标集比 → **删多余** → **补缺失**」，直到**内容集逐条相等**；
   ③ **别用 `modify`**，用「删该段 + `pcb_PrimitiveLine.create` 重建」；
   ④ 零漂移判据是**内容**口径（id 变不算漂移）。
   另：**画布 mil 的 y = (30 − 任务系 y_mm) × 39.37**（y 是翻转的）——漏翻转会一次改歪整条链
   （本次事故即由此引起，靠迭代回滚按内容重建复原）。
121. **回滚/撤换要按「内容口径」重建，不能按 id 复原**（2026-10-22 flexiheal-v2 G4R 实测）：
   一次误操作改歪了 6 段、丢失 4 段、多出 9 段；因段 id 已变，**按 id 删回是删不掉的**。
   可行做法：以**基线 dump 的内容集**为目标，用「删多余 + 补缺失」的迭代法复原，
   迭代 2~3 轮即完全回到基线（实测 212 段内容集与基线**逐条全等**）。
   **备份要先于动手**：任何批量改动前先落一份**全量 dump**（含走线/过孔/焊盘/铺铜/文本），
   它是唯一可靠的回滚依据。

122. **批量 Markdown→LaTeX 转换会“连锁错位”，且报错与病因完全无关**（2026-10-22 flexiheal-v2 讲义站实测）：把讲义源文件里成对的 `**粗体**` 批量转成 `\textbf{}` 时，只要源文件里**有一处 `**` 写错**（少一个闭合、或与 `\textbf{}` 混写），转换器就会把**其后所有**粗体配对整体错位一格，产出 `}TEXT\textbf{` 的坏形态；随后 LaTeX 报的是 "Extra }, or forgotten \endgroup"、"Missing $ inserted"、"Undefined control sequence"、"Counter too large" 等**看似毫不相关**的错误（本站在本轮连续遇到 4 类，其中 "Counter too large" 最终查出来是**页码字母制**超 26 页，与计数器毫无关系）。**纪律**：① 批量转换**前**先跑三个体检脚本——花括号平衡、行内数学 `$` 配平、正文模式未转义 `_`；② 转换**后**再跑一遍同样的三个脚本；③ 转换器要**按行**处理并对未闭合的 `**` **报错而不是静默配对**（本项目用的脚本见 `projects/flexiheal-v2/scripts/g7/g7_markdown.py` 与 `g7_bracecheck.py` / `g7_mathcheck.py` / `g7_undscore.py`）。

123. **`xelatex` 报 "Counter too large" 时，先查页码格式再查计数器**（2026-10-22 flexiheal-v2 讲义站实测）：ElegantBook 的 `\maketitle` 内部会调用 `\pagenumbering{Alph}`，页码用 A--Z 字母，而 LaTeX 的 `\@Alph` 只有 26 个符号 ⇒ **讲义超过 26 页就报 "Counter too large"**（`\@ctrerr`），报错位置还会落在当时恰好在排版的那一行，看起来像某个环境（tcolorbox / table）坏了。**处置**：在 `\maketitle` **之后**加 `\pagenumbering{arabic}`（写在 `\maketitle` 之前会被它覆盖），并把"页码制"列入"讲义类文档必查项"。

124. **讲义的附图必须与"工程最后状态"绑定，且过期图的判定要看内容而不只是时间戳**（2026-10-22 flexiheal-v2 讲义站实测）：本项目的 G2b 原理图截图（10-21 22:52）虽然"复制到交付目录"后时间戳变成最新的，但**内容早于 D042（J1 由 4 脚换 2 脚）与 D072（R1 还原）**——图上是旧 J1，属于"时间戳新、内容旧"的过期图，只看时间戳会漏判。**纪律**：① 附图**优先用官方导出 API 现导**（本项目用 `eda.sch_ManufactureData.getPngFile(fileName,{width,height})`，分辨率上限 4096），不依赖历史截图；② 每一张图在 `PROVENANCE.md` 里登记 **来源方法 + 采集时刻 + 展示的工程状态（哪一阶段的板子）+ 回链的决策编号** 四项；③ 板级图要按**阶段**分工（布局阶段（铺铜前）/ 布线终态（铺铜前）/ 终态（铺铜后）），同一张图里并排出现不同阶段的图时**图注必须写明阶段**；④ 板子被修正站改过之后，**受影响的图必须重新渲染**（本项目 G4R 改过两个过孔后，`_g3_analog_afe.png` 被覆盖、另出 `_g4r_via_migration.png` 与 `_g4r_decoupling_c3c4.png`，讲义站随之换图并在正文用终态数字）。

125. **“行首裸 LaTeX 命令字面量”是一类编译与质检都抓不到的缺陷，必须单独设规则**（2026-10-22 flexiheal-v2 讲义站实测）：批量脚本把讲义源文件里的 `\footnotesize` 改成 `\footnotesize` 时，如果字符串里写成 `"\footnotesize"`（Python 会把 `\t` 解释成 **TAB**）或 `"\footnotesize"`（`\f` 在某些写法下被吃掉），生成的 tex 里就只剩**裸的 `footnotesize`**——LaTeX 把它当成**普通文字**印在页面上，**编译零报错、`tex_quality_check.py` 零告警**，只有把 PDF 渲染成 PNG 逐页看才会发现（本项目一次就漏了 **22 处、跨 20 页**）。**纪律**：① 质检器加 **error 级**规则 `BARE_CMD_LITERAL`：扫描**行首**（允许空白缩进）出现裸 `footnotesize`/`toprule`/`tabular`/`itemize`/`centering`/`hline`/`midrule`/`bottomrule`/`multicolumn`/`cline`/`linewidth` 等**命令字面量**（前面不是 `\`）即报错；② 脚本里凡是要输出反斜杠，一律用 `chr(92)` 或 `@BS@` 占位再统一替换，**不要在字符串字面量里直接写 `\`**（`\t` → TAB、`\f` → 换页符，是两个真实的坑）；③ 改完必须重跑**回归自检**（本项目 `scripts/g7/check_texcheck_regression.py` 已补 9 个用例），确认**检查没被改弱**。这条与 G6 的"证据通过 ≠ 方法有效"同源：**一个门没报，不等于没问题，只等于这个门测不到那一类。**
126. **"每个数值都要回链"的教学要求，会把讲义逼成工作日志**（2026-10-22 flexiheal-v2 委托方定性意见）：讲义站按"每个数值/公式可回链到决策日志与验证记录"的硬要求逐条落实，结果**60 页讲义里混进了 342 处过程性内容**（`D0xx` 决策编号、`reports/…` 证据路径、`豁免 W-0x`、"G4R 站"、"委托方要求"、"本 session"、脚本/质检器自述），而**真正的电子设计词只有 1593 个**——委托方评价"**这更像工作日志而非讲义**"。**根因**：**"可追溯"被误实现成"把追溯标识直接印在正文里"**。追溯是**内部质量保证手段**（用来证明数字不是编的），**不是读者需要的内容**。**正确做法**：① 追溯在**内部**做（grep 决策日志/验证记录确认每个数字有出处），**只把结论印进讲义**（"实测：最小间隙 0.522\,mm" 而不是 "见 D047 与 reports/xxx.json"）；② **区分两类"约束"**——**电子设计约束要留**（线宽分档、板边距、禁铜区、不换层、手焊工艺，这些是读者要学的**设计规范**），**管理约束要删**（决策编号、豁免流程、证据路径、谁拍板，这些是**我们的内部过程**）；③ **错例警示盒的叙事视角要换成读者**（"**你**容易在哪错"而不是"**我们**当初错在哪"，技术内核保留）；④ **把内容边界做成可量化的自查**（六类模式扫描 + 命中数上限），别靠感觉。**教训**：**任何"每个 X 都要标注 Y"的要求，都要先问"Y 是读者要的信息吗"**——如果 Y 是内部工作痕迹，标注 Y 本身就是在污染交付物。
127. **"统计脚本报了 0"要先问"它统计了哪些区域"**（2026-10-22 flexiheal-v2 讲义去工作流化实测）：讲义改写后用自写脚本统计"过程性内容"命中数，**报 0**，但编译出的 PDF 里**仍有 36 行**残留（D 编号、`G5F 线宽实测`、`IR 约束 C5`…）。**根因**：统计脚本为了避免表格噪声，在扫描前**把 `tabular` 整块剥掉了** —— 而**表格恰恰是编号最密集的地方**（"依据/证据"列几乎全是 D 编号与站名）。于是"正文干净"的结论掩盖了"表格没看"。**对策**：① **统计口径必须覆盖最终交付物的全部可见区域**——文本层 = 正文 + 表格 + 图注 + 标题，**任何一处被排除就要在输出里明说**；② **正文与表格分开报**（表格通常更脏，因为表格天生爱放"出处"列）；③ **扫描完必须对最终产物（PDF）反向验一次**——直接搜 PDF 文本层里的 `D\d{3}`/`reports/`/`豁免`，那是读者真正看到的东西；④ **"证据通过 ≠ 方法有效"（#115）的又一例**：数字漂亮是因为**量测函数没覆盖那类图元**，这次是"没覆盖表格"。
128. **LaTeX 命令字面量泄漏有"少一个反斜杠"和"多一个反斜杠"两个方向，质检要两个都覆盖**（2026-10-22 flexiheal-v2 讲义终检实测）：① **少**一个：`\footnotesize` 写成 `footnotesize` → 整段文字被当正文印出（D102 已加 `BARE_CMD_LITERAL` 规则）；② **多**一个：`\Rightarrow` 写成 `\Rightarrow` → LaTeX 里 `\` 是**换行**，于是变成"换行 + 文字 Rightarrow"，**命令名照样印在页面上**（D105 已加 `DOUBLE_BACKSLASH_CMD` 规则）。**两者共同特征：编译不报错、静态检查不报，只有把 PDF 渲染出来看文本层才发现**。**排除合法用法要用"语义白名单"**——多反斜杠规则不能简单地禁掉 `\`（TikZ 节点里的 `{SAADC\P0.02/AIN0}` 是**合法换行**），只能禁"双反斜杠 + **已知 LaTeX 命令名**"。**配套扫描器**：`scripts/g7/g7_dblslash_scan.py`（专查此类写法，改前 2 处、改正后仅剩 5 处合法 TikZ 换行）。**教训**：**凡是"拼写/转义错误会导致字面量被印出来"的缺陷，都要有自动规则 + 回归用例**，否则只能靠人眼逐页看——而人眼会漏。
129. **验收轮次本身要设预算，否则会把交付拖成马拉松**（2026-10-22 委托方意见固化）：本讲义的逐页视觉评审做了 **5 轮**（首轮抓出 22 处 `footnotesize` 泄漏 + 3 处 `**` 泄漏 + 4 处跨页断裂 = 真正的系统性缺陷；第 2~5 轮抓的都是 minor：留白比例、表格列宽、个别标注贴边、厂商图例裁切 1 个字）。委托方明确指出"**每次的 final-check 轮的检查实在是太冗长了**"。**教训**：① **首轮全本、后续只查改动页**（改动页 + 相邻页），不重扫全本；② **收敛信号 = 同一类缺陷连续两轮无新增 ⇒ 停止评审进交付**，剩下的 minor 如实写进遗留表即可；③ **一个交付物最多 3 轮评审**，到第 3 轮还有 major 就**如实上报委托方裁决**，而不是继续无限修；④ **"修到完美"不是目标**——评审的价值在首轮抓系统性缺陷，次要排版瑕疵不值得再开一轮全本扫描。**判据**：`严重度 ≥ major 的缺陷数` 与 `评审轮次` 都应单调下降；一旦前者不再下降，就该收口。
