import sys, struct
from capstone import *
d=open(sys.argv[1],'rb').read()
start=int(sys.argv[2],0); end=int(sys.argv[3],0)
md=Cs(CS_ARCH_M68K, CS_MODE_BIG_ENDIAN|CS_MODE_M68K_000)
pc=start
while pc<end:
    ok=False
    for i in md.disasm(d[pc:end], pc):
        b=' '.join('%02x'%c for c in i.bytes)
        print('%06x: %-20s %-9s %s'%(i.address,b,i.mnemonic,i.op_str))
        pc=i.address+i.size; ok=True
    if not ok:
        print('%06x: %-20s DC.W      $%04x'%(pc,'%02x %02x'%(d[pc],d[pc+1]),struct.unpack('>H',d[pc:pc+2])[0]))
        pc+=2
