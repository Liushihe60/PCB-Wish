* buck_div.sp — TPS40200 分立级 BUCK 行为级 testbench（种子块 buck-tps40200）
* 目的：验证功率级量级（输出包络/纹波量级/电感平均电流），
*       不含 TPS40200 闭环行为（无厂商模型，禁止伪造）→ 结论记 COMPUTED。
* 工作点：Vin=12V，Vout=5V/1A（Rload=5Ω），fsw=300kHz，D=5/12≈0.4167，
*         L=33uH，Cout=100uF（ΔI_L≈0.29A，ΔV_C≈1.2mV+ESR，包络判据留裕量）。
* 运行: testbench_run.py --testbench buck_div.sp --criteria criteria.json
* TODO(装好 ngspice 后联调)：语法与量级复核；必要时调 PULSE 宽度容差。
.option TEMP=25
VIN VIN 0 DC 12

* PWM 驱动：300kHz，占空比 0.4167（ton=1.389us）
VPWM PWM 0 PULSE(0 5 5ms 20n 20n 1.389u 3.3333u)

* 功率开关：压控开关近似 FDC654P（RON=30mΩ）
S1 SWH SW PWM 0 SWMOD
.model SWMOD SW(VT=2.5 RON=0.03 ROFF=1e6)

* 续流二极管 1N5822（行为级参数）
D1 0 SW DMOD
.model DMOD D(IS=1u RS=0.02)

* 储能电感 33uH + 输出电容 100uF（ESR 50mΩ 折算）+ 负载 5Ω
L1 SWH VOUT 33u
COUT VOUT 0 100u
RL VOUT 0 5

* 5ms 稳定后测 30-40ms 窗口（覆盖软启动后的稳态）
.tran 1u 40m 5m 10u
* 输出平均值（精度：4.85-5.15V，课程任务书 ±3% 放裕量）
.meas TRAN vout_avg AVG V(VOUT) FROM=30ms TO=40ms
* 纹波包络（含 ESR 尖峰，判据 ±0.25V 裕量）
.meas TRAN vout_max MAX V(VOUT) FROM=30ms TO=40ms
.meas TRAN vout_min MIN V(VOUT) FROM=30ms TO=40ms
* 电感平均电流 ≈ 负载电流 1A
.meas TRAN i_l_avg AVG I(L1) FROM=30ms TO=40ms

.end
