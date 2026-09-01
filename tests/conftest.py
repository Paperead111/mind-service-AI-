"""pytest 全局配置：让测试环境不触发 R0 Key 探测、不启动常驻生命循环。

- R0_KEY_PROBE=false：TestClient 的 lifespan 不做网络连通性探测（测试必须离线）
- LIFE_LOOP_ENABLED=false：lifespan 不挂 60s 后台 tick（测试自建 LifeLoop 手动 run_once）
"""
import os

os.environ["R0_KEY_PROBE"] = "false"
os.environ["LIFE_LOOP_ENABLED"] = "false"
