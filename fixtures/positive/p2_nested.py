"""嵌套结构样例：校验所在结构定位到 method / nested。"""


class Service:
    def __init__(self, items=set()):
        self.items = items

    def rebuild(self):
        def inner(cache={}):
            return cache
        return inner()

    def find(self, key):
        try:
            return self.items[key]
        except KeyError:
            pass

    def stop(self):
        return
        self.items = None
