import re

class CompileError(Exception): pass


class Parser:
    def __init__(self, src):
        self.tokens = re.findall(
            r'\d+|"[^"]*"|[a-zA-Z_]\w*|[{}();,+\-*/<>=!]', src)
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ""
    def next(self): t=self.peek(); self.pos+=1; return t
    def expect(self,t):
        if self.next()!=t: raise CompileError(f"expect {t}")

    def program(self):
        funcs = {}
        while self.peek():
            funcs.update(self.func())
        return funcs

    def func(self):
        self.expect("fun"); name=self.next(); self.expect("(")
        params=[]
        if self.peek()!=")":
            while True:
                p=self.next(); params.append(p)
                if self.peek()==":": self.next(); self.next()
                if self.peek()==",": self.next(); continue
                break
        self.expect(")")
        if self.peek()==":": self.next(); self.next()
        self.expect("{")
        body = self.stmt()
        self.expect("}")
        return {name: {"params": params, "body": body}}

    def stmt(self):
        if self.peek()=="return":
            self.next(); e=self.expr(); self.expect(";"); return ("return", e)
        e=self.expr(); self.expect(";"); return ("expr", e)

    def expr(self):
        left = self.term()
        while self.peek() in list("+-*/<>=!"):
            op=self.next()
            if op=="=" and self.peek()=="=": op="=="; self.next()
            right = self.term()
            left = ("bin", op, left, right)
        return left

    def term(self):
        t = self.next()
        if re.match(r"^-?\d+$", t): return ("num", int(t))
        if t.startswith('"'): return ("str", t.strip('"'))
        if t=="(": e=self.expr(); self.expect(")"); return e
        if re.match(r"[a-zA-Z_]\w*", t):
            if self.peek()=="(":
                self.next(); args=[]
                if self.peek()!=")":
                    while True:
                        args.append(self.expr())
                        if self.peek()==",": self.next(); continue
                        break
                self.expect(")")
                return ("call", t, args)
            return ("var", t)
        raise CompileError(f"unexpected {t}")


def _expr(e):
    if e[0]=="num": return [("PUSH", e[1])]
    if e[0]=="str": return [("PUSH", e[1])]
    if e[0]=="var": return [("PUSH", 0)]
    if e[0]=="bin":
        ops = _expr(e[2]) + _expr(e[3])
        m = {"+":"ADD","-":"SUB","*":"MUL","/":"DIV",
             "<":"LT",">":"GT","=":"EQ","==":"EQ","!=":"NEQ"}
        ops.append((m.get(e[1],"NOP"),))
        return ops
    if e[0]=="call":
        ops = []
        for a in e[2]: ops += _expr(a)
        return ops
    return []


def _stmt(node):
    if node[0]=="return":
        return _expr(node[1]) + [("RETURN",)]
    return _expr(node[1]) + [("POP",)]


def compile_tolk(src):
    p = Parser(src)
    funcs = p.program()
    return {n: _stmt(f["body"]) for n, f in funcs.items()}


def run_tolk(src, fn=None, args=None, gas=1_000_000, storage=None):
    from .tvm import TVM
    bc = compile_tolk(src)
    fn = fn or list(bc.keys())[0]
    vm = TVM(gas=gas)
    ret = vm.execute(bc[fn], args or [], storage)
    return {"ok": True, "returned": ret, "stack": vm.stack,
            "storage": vm.storage, "gas_used": vm.gas_used,
            "logs": vm.logs, "bytecode": bc[fn]}