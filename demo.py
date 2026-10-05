"""demo.py — 生成 README 所需的解析轨迹 / 环报告 / 回收轨迹演示输出。"""

from di import Container, CycleError, ConstructionError


def sep(title):
    print(f"\n{'='*10} {title} {'='*10}")


# 1) 线性链 + 无依赖：解析顺序与轨迹
sep("1. 线性链解析轨迹")
c = Container()
c.register("logger", lambda: "Logger()")
c.register("db", lambda logger: f"DB({logger})", deps={"logger": "logger"})
c.register("repo", lambda db: f"Repo({db})", deps={"db": "db"})
c.register("svc", lambda repo, logger: f"Svc({repo},{logger})",
           deps={"repo": "repo", "logger": "logger"})
print("解析顺序:", " -> ".join(c.resolution_order(["svc"])))
c.tracer.clear()
c.resolve("svc")
print(c.tracer.render())

# 2) 循环依赖：环报告
sep("2. 循环依赖环报告")
c2 = Container()
c2.register("a", lambda b: b, deps={"b": "b"})
c2.register("b", lambda cc: cc, deps={"cc": "c"})
c2.register("c", lambda a: a, deps={"a": "a"})
c2.register("d", lambda b: b, deps={"b": "b"})   # 在环外
c2.register("x", lambda y: y, deps={"y": "y"})
c2.register("y", lambda x: x, deps={"x": "x"})
try:
    c2.resolve("d")
except CycleError as e:
    print(e.report)
print("第二个环:")
print(c2.cycle_report(["x"]))

# 3) 延迟注入：取值时机轨迹
sep("3. 延迟注入取值时机")
c3 = Container()
c3.register("heavy", lambda: "HeavyResource")
c3.register("handler", lambda heavy_ref: heavy_ref, lazy={"heavy_ref": "heavy"})
h = c3.resolve("handler")
print("--- 构造完成后 heavy 尚未解析，下面首次调用 get() ---")
h.get()
h.get()
print(c3.tracer.render())

# 4) 构造失败：逆序回收轨迹
sep("4. 构造失败回收轨迹")
c4 = Container()
closed = []
class Res:
    def __init__(self, n): self.n = n
    def close(self): closed.append(self.n)
    def __repr__(self): return f"Res({self.n})"
c4.register("conn", lambda: Res("conn"))
c4.register("pool", lambda conn: Res("pool"), deps={"conn": "conn"})
c4.register("cache", lambda pool: Res("cache"), deps={"pool": "pool"})
def boom(cache):
    raise RuntimeError("磁盘已满")
c4.register("ingest", boom, deps={"cache": "cache"})
try:
    c4.resolve("ingest")
except ConstructionError as e:
    print(c4.tracer.render())
    print(f"\n回收清单(cleaned): {e.cleaned}")
    print(f"close() 调用顺序 : {closed}")
    print(f"失败后容器内残留单例: {list(c4._singletons)}")
