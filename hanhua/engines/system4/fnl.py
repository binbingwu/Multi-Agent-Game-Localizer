import struct,zlib
def idx2char(i):
    if i<95: return i+0x20
    if i<158: return i-95+0xA1
    i-=158; f=0x81+i//188
    if f>=0xa0: f+=31
    s=0x40+i%188
    if s>=0x7f: s+=1
    return (f<<8)|s
def char2idx(c):
    if c<0x20: return 0
    if c<0x7f: return c-0x20
    if c<0xa1: return 0
    if c<0xe0: return c-0x42
    f=c>>8; s=c&0xff
    if s<0x40 or s==0x7f or s>0xfc: return 0
    si=s-(0x40+(1 if s>0x7f else 0))
    if f<0x81: return 0
    elif f<0xa0: fi=f-0x81
    elif f<0xe0: return 0
    elif f<0xfd: fi=f-0xe0+31
    else: return 0
    return 158+fi*188+si
def load(path):
    d=open(path,'rb').read()
    assert d[:4]==b'FNA\0'
    uk,fs,isz=struct.unpack('<III',d[4:16])
    p=16
    nf,=struct.unpack('<I',d[p:p+4]);p+=4
    fonts=[]
    for _ in range(nf):
        nface,=struct.unpack('<I',d[p:p+4]);p+=4
        faces=[]
        for _ in range(nface):
            h,uk2,ng=struct.unpack('<III',d[p:p+12]);p+=12
            gl=[]
            for _ in range(ng):
                w,pos,cs=struct.unpack('<HII',d[p:p+10]);p+=10
                gl.append([w,pos,cs])
            faces.append(dict(h=h,uk=uk2,g=gl))
        fonts.append(faces)
    assert p==isz,(p,isz)
    return d,fonts
def glyph(d,h,g):
    w,pos,cs=g
    if not pos: return None
    stride=(w+31)//32*4
    raw=zlib.decompress(d[pos:pos+cs])
    raw=raw+b'\0'*(stride*h-len(raw))
    return w,stride,raw
