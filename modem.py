"""
Modem acústico MFSK compartilhado entre emissor (enviar.py) e receptor (receber.py).

Formato de cada pacote no ar:
    [chirp de sincronismo 100 ms][silêncio 20 ms][182 símbolos MFSK][silêncio 40 ms]

Cada símbolo = 1 de 16 tons (4 bits), 12 ms (2 ms de guarda contra eco + 10 ms de análise).
Os tons são múltiplos de 100 Hz, então ficam ortogonais na janela de 10 ms (sem vazamento na FFT).

Dados do pacote (91 bytes):
    cabeçalho (sessão, seq, total, tamanho) 7 B + payload 64 B + CRC32 4 B + Reed-Solomon 16 B
O Reed-Solomon corrige até 8 bytes errados por pacote; o CRC descarta o que sobrar.
"""
import struct
import zlib

import numpy as np
from scipy.signal import chirp, fftconvolve
from reedsolo import RSCodec, ReedSolomonError

SR_PADRAO = 48000

N_TONS = 16                 # 16 tons -> 4 bits por símbolo
F_BASE = 1000.0             # Hz
F_PASSO = 100.0             # Hz (= 1 / JANELA -> ortogonalidade)
JANELA = 0.010              # s, janela de análise
GUARDA = 0.002              # s, descartado na recepção (eco/reverberação)
SIMBOLO = JANELA + GUARDA

PREAMBULO = 0.100
SILENCIO_POS_PREAMBULO = 0.020
SILENCIO_ENTRE_PACOTES = 0.040
AMPLITUDE = 0.5
LIMIAR_SYNC = 0.40          # correlação normalizada mínima para aceitar o chirp

PAYLOAD = 64
CABECALHO = struct.Struct(">HHHB")   # sessão, seq, total, tamanho útil
NSYM_RS = 16
_rs = RSCodec(NSYM_RS)

BYTES_PACOTE = CABECALHO.size + PAYLOAD + 4 + NSYM_RS
SIMBOLOS_PACOTE = BYTES_PACOTE * 2
MAX_PACOTES = 65535


def amostras(segundos, sr):
    return int(round(segundos * sr))


def frequencias():
    return F_BASE + F_PASSO * np.arange(N_TONS)


def preambulo(sr):
    n = amostras(PREAMBULO, sr)
    t = np.arange(n) / sr
    sinal = chirp(t, f0=800, f1=3500, t1=PREAMBULO, method="linear")
    return (AMPLITUDE * sinal * np.hanning(n)).astype(np.float32)


# ---------------------------------------------------------------- pacotes

def montar_pacote(sessao, seq, total, dados):
    corpo = CABECALHO.pack(sessao, seq, total, len(dados)) + dados.ljust(PAYLOAD, b"\0")
    corpo += struct.pack(">I", zlib.crc32(corpo))
    return bytes(_rs.encode(corpo))


def abrir_pacote(bruto):
    """Retorna (sessao, seq, total, dados) ou None se o pacote estiver corrompido."""
    try:
        r = _rs.decode(bytearray(bruto))
        corpo = bytes(r[0] if isinstance(r, tuple) else r)
    except ReedSolomonError:
        return None
    conteudo, crc = corpo[:-4], struct.unpack(">I", corpo[-4:])[0]
    if zlib.crc32(conteudo) != crc:
        return None
    sessao, seq, total, n = CABECALHO.unpack(conteudo[:CABECALHO.size])
    if n > PAYLOAD or seq >= total:
        return None
    return sessao, seq, total, conteudo[CABECALHO.size:CABECALHO.size + n]


# ---------------------------------------------------------------- modulação

def modular(bruto, sr):
    """Bytes do pacote -> áudio float32 (preâmbulo + símbolos + silêncios)."""
    simbolos = []
    for b in bruto:
        simbolos += [b >> 4, b & 0x0F]
    n_sim = amostras(SIMBOLO, sr)
    f_inst = np.repeat(frequencias()[simbolos], n_sim)
    fase = 2 * np.pi * np.cumsum(f_inst) / sr          # fase contínua: sem estalos
    dados = AMPLITUDE * np.sin(fase)
    rampa = amostras(0.003, sr)
    dados[:rampa] *= np.linspace(0, 1, rampa)
    dados[-rampa:] *= np.linspace(1, 0, rampa)
    return np.concatenate([
        preambulo(sr),
        np.zeros(amostras(SILENCIO_POS_PREAMBULO, sr)),
        dados,
        np.zeros(amostras(SILENCIO_ENTRE_PACOTES, sr)),
    ]).astype(np.float32)


def demodular(trecho, sr):
    """Áudio já alinhado no início dos símbolos -> bytes do pacote."""
    n_sim = amostras(SIMBOLO, sr)
    n_jan = amostras(JANELA, sr)
    blocos = trecho[: SIMBOLOS_PACOTE * n_sim].reshape(SIMBOLOS_PACOTE, n_sim)[:, n_sim - n_jan:]
    espectro = np.abs(np.fft.rfft(blocos, axis=1))
    bins = np.round(frequencias() / (sr / n_jan)).astype(int)
    simbolos = np.argmax(espectro[:, bins], axis=1)
    return bytes(((simbolos[0::2] << 4) | simbolos[1::2]).astype(np.uint8))


# ---------------------------------------------------------------- sincronismo

class Sincronizador:
    """Recebe áudio em blocos, acha o chirp e entrega os bytes brutos de cada pacote."""

    def __init__(self, sr, ao_pacote):
        self.sr = sr
        self.ao_pacote = ao_pacote
        self.pre = preambulo(sr)
        self.L = len(self.pre)
        self.norma_pre = float(np.linalg.norm(self.pre))
        self.n_gap = amostras(SILENCIO_POS_PREAMBULO, sr)
        self.n_dados = SIMBOLOS_PACOTE * amostras(SIMBOLO, sr)
        self.busca = amostras(0.5, sr)
        self.buf = np.zeros(0, dtype=np.float32)

    def alimentar(self, x):
        self.buf = np.concatenate([self.buf, np.asarray(x, dtype=np.float32)])
        while len(self.buf) >= self.busca + self.L:
            regiao = self.buf[: self.busca + self.L].astype(np.float64)
            corr = np.abs(fftconvolve(regiao, self.pre[::-1], mode="valid"))
            acum = np.concatenate(([0.0], np.cumsum(regiao ** 2)))
            energia = acum[self.L:] - acum[:-self.L]
            ncc = corr / (self.norma_pre * np.sqrt(np.maximum(energia, 1e-12)))
            ncc[energia < 1e-7 * self.L] = 0.0
            i = int(np.argmax(ncc))

            if ncc[i] < LIMIAR_SYNC:
                self.buf = self.buf[self.busca:]          # nada aqui, descarta
                continue

            inicio = i + self.L + self.n_gap
            fim = inicio + self.n_dados
            if len(self.buf) < fim:
                return                                   # espera o resto do pacote
            self.ao_pacote(demodular(self.buf[inicio:fim], self.sr))
            self.buf = self.buf[fim:]
