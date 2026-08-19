import struct, sys

def u32(d,o): return struct.unpack('>I',d[o:o+4])[0]

def parse(path):
    d=open(path,'rb').read()
    o=0
    assert u32(d,0)==0x3f3, hex(u32(d,0))
    o=4
    if u32(d,o)!=0: raise Exception('res libs')
    o+=4
    tbl=u32(d,o); first=u32(d,o+4); last=u32(d,o+8); o+=12
    sizes=[u32(d,o+4*i) for i in range(last-first+1)]
    o+=4*(last-first+1)
    print('hunks',tbl,'sizes',[hex(s*4) for s in sizes])
    hunks=[]
    cur=None
    while o<len(d):
        t=u32(d,o); o+=4
        tt=t&0x3fffffff
        if tt==0x3e9 or tt==0x3ea or tt==0x3eb: # code,data,bss
            n=u32(d,o); o+=4
            if tt==0x3eb:
                body=b'\0'*(n*4); 
            else:
                body=d[o:o+n*4]; o+=n*4
            cur={'type':{0x3e9:'CODE',0x3ea:'DATA',0x3eb:'BSS'}[tt],'data':bytearray(body),'reloc':[],'off':None}
            hunks.append(cur)
        elif tt==0x3ec: # reloc32
            while True:
                cnt=u32(d,o); o+=4
                if cnt==0: break
                h=u32(d,o); o+=4
                for i in range(cnt):
                    cur['reloc'].append((u32(d,o),h)); o+=4
        elif tt==0x3f2: # end
            pass
        elif tt==0x3f0: # symbol/debug
            n=u32(d,o); o+=4; o+=n*4
        elif tt==0x3f1:
            n=u32(d,o); o+=4; o+=n*4
        else:
            print('unknown hunk type %08x at %x'%(t,o-4)); break
    return hunks

if __name__=='__main__':
    hs=parse(sys.argv[1])
    base=0
    for i,h in enumerate(hs):
        h['off']=base; base+=len(h['data'])
        print(i,h['type'],'len',hex(len(h['data'])),'relocs',len(h['reloc']),'base',hex(h['off']))
    # flatten with relocation applied (contiguous layout)
    img=bytearray()
    for h in hs: img+=h['data']
    for i,h in enumerate(hs):
        for (ofs,tgt) in h['reloc']:
            p=h['off']+ofs
            v=struct.unpack('>I',img[p:p+4])[0]+hs[tgt]['off']
            img[p:p+4]=struct.pack('>I',v)
    open(sys.argv[2],'wb').write(img)
    print('wrote',sys.argv[2],hex(len(img)))
