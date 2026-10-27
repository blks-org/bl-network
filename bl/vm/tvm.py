from .cell import Cell

class VMError(Exception): pass
class OutOfGas(VMError): pass

GAS = {"PUSH":1,"POP":1,"DUP":1,"SWAP":1,"OVER":1,"ROT":2,
       "ADD":2,"SUB":2,"MUL":3,"DIV":3,"MOD":3,
       "EQ":2,"NEQ":2,"LT":2,"GT":2,"LE":2,"GE":2,
       "AND":2,"OR":2,"NOT":1,"XOR":2,"IF":2,"JMP":1,"JMPI":2,
       "CELL_NEW":10,"CELL_LOAD":15,"CELL_HASH":25,"CELL_EQ":20,
       "TUPLE_NEW":5,"TUPLE_LEN":3,"TUPLE_AT":5,
       "STORE":5,"LOAD":5,"LOG":5,"RETURN":1,"STOP":1,"NOP":0}


class TVM:
    def __init__(self, gas=1_000_000):
        self.stack=[]; self.gas=gas; self.gas_used=0
        self.logs=[]; self.returned=None; self.halted=False
        self.pc=0; self.code=[]; self.storage={}

    def push(self,v): self.stack.append(v)
    def pop(self):
        if not self.stack: raise VMError("underflow")
        return self.stack.pop()
    def peek(self):
        if not self.stack: raise VMError("underflow")
        return self.stack[-1]

    def _burn(self,op):
        c = GAS.get(op,1)
        if self.gas < c: raise OutOfGas(f"gas: {op}")
        self.gas -= c; self.gas_used += c

    def execute(self, code, args=None, storage=None):
        self.code = list(code); self.pc = 0; self.halted = False
        self.returned = None; self.storage = dict(storage or {})
        for a in (args or []): self.push(a)
        while not self.halted and self.pc < len(self.code):
            instr = self.code[self.pc]
            self._burn(instr[0])
            self._dispatch(instr)
            self.pc += 1
        return self.returned

    def _dispatch(self, i):
        op = i[0]
        if op=="PUSH": self.push(i[1])
        elif op=="POP": self.pop()
        elif op=="DUP": self.push(self.peek())
        elif op=="SWAP":
            a=self.pop(); b=self.pop(); self.push(a); self.push(b)
        elif op=="OVER": self.push(self.stack[-2])
        elif op=="ROT":
            a=self.pop(); b=self.pop(); c=self.pop()
            self.push(b); self.push(a); self.push(c)
        elif op=="ADD": b=self.pop(); a=self.pop(); self.push(a+b)
        elif op=="SUB": b=self.pop(); a=self.pop(); self.push(a-b)
        elif op=="MUL": b=self.pop(); a=self.pop(); self.push(a*b)
        elif op=="DIV": b=self.pop(); a=self.pop(); self.push(a//b if b else 0)
        elif op=="MOD": b=self.pop(); a=self.pop(); self.push(a%b if b else 0)
        elif op=="EQ": b=self.pop(); a=self.pop(); self.push(int(a==b))
        elif op=="NEQ": b=self.pop(); a=self.pop(); self.push(int(a!=b))
        elif op=="LT": b=self.pop(); a=self.pop(); self.push(int(a<b))
        elif op=="GT": b=self.pop(); a=self.pop(); self.push(int(a>b))
        elif op=="LE": b=self.pop(); a=self.pop(); self.push(int(a<=b))
        elif op=="GE": b=self.pop(); a=self.pop(); self.push(int(a>=b))
        elif op=="AND": b=self.pop(); a=self.pop(); self.push(int(bool(a and b)))
        elif op=="OR": b=self.pop(); a=self.pop(); self.push(int(bool(a or b)))
        elif op=="NOT": self.push(0 if self.pop() else 1)
        elif op=="XOR": b=self.pop(); a=self.pop(); self.push(int(bool(a)^bool(b)))
        elif op=="IF":
            if not self.pop(): self.pc = i[1]-1
        elif op=="JMP": self.pc = i[1]-1
        elif op=="JMPI": self.pc = self.pop()-1
        elif op=="CELL_NEW":
            n=self.pop(); vals=[self.pop() for _ in range(n)]
            bits=b""; refs=[]
            for v in vals:
                if isinstance(v,Cell): refs.append(v)
                elif isinstance(v,int): bits += v.to_bytes(8,"big",signed=True)
                elif isinstance(v,str): bits += v.encode()[:64]
            self.push(Cell(bits, refs))
        elif op=="CELL_LOAD": self.push(self.pop().bits)
        elif op=="CELL_HASH": self.push(self.pop().hash_hex())
        elif op=="CELL_EQ": b=self.pop(); a=self.pop(); self.push(int(a==b))
        elif op=="TUPLE_NEW":
            n=self.pop(); vals=[self.pop() for _ in range(n)]
            self.push(list(reversed(vals)))
        elif op=="TUPLE_LEN": self.push(len(self.pop()))
        elif op=="TUPLE_AT":
            j=self.pop(); t=self.pop(); self.push(t[j])
        elif op=="STORE":
            k=self.pop(); v=self.pop(); self.storage[k]=v
        elif op=="LOAD":
            k=self.pop(); self.push(self.storage.get(k,0))
        elif op=="LOG": self.logs.append(repr(self.pop()))
        elif op=="RETURN":
            self.returned = self.pop() if self.stack else None
            self.halted = True
        elif op=="STOP": self.halted = True
        elif op=="NOP": pass
        else: raise VMError(f"unknown op: {op}")