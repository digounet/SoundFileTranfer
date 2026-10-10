"""
Modem acústico MFSK compartilhado entre emissor (enviar.py) e receptor (receber.py).

Formato de cada pacote no ar:
    [chirp de sincronismo 100 ms][silêncio 20 ms][91 símbolos MFSK][silêncio 40 ms]

Cada símbolo dura 12 ms (2 ms de guarda contra eco + 10 ms de análise) e carrega 2 bancos de
16 tons tocando ao mesmo tempo: cada banco escolhe 1 de 16 tons (4 bits), logo 8 bits por
símbolo. Os tons são múltiplos de 100 Hz (1000 a 4100 Hz), ortogonais na janela de 10 ms.
A potência total é a mesma de um tom só; cada banco fica 3 dB abaixo.

Dados do pacote (91 bytes):
    cabeçalho (sessão, seq, total, tamanho) 7 B + payload 64 B + CRC32 4 B + Reed-Solomon 16 B
O Reed-Solomon corrige até 8 bytes errados por pacote; o CRC descarta o que sobrar.

Recepção: o áudio passa por um passa-banda na faixa útil antes de tudo. Isso tira ruído e
cliques fora da banda e é o que permite um limiar de chirp baixo sem falsos positivos.
"""
import struct
import zlib

import numpy as np
from scipy.signal import chirp, fftconvolve, firwin, lfilter
from reedsolo import RSCodec, ReedSolomonError

SR_PADRAO = 48000

N_TONS = 16                 # 16 tons -> 4 bits por banco
N_BANCOS = 2                # bancos tocando em paralelo; cada um leva um nibble por símbolo
F_BASE = 1000.0             # Hz
F_PASSO = 100.0             # Hz (= 1 / JANELA -> ortogonalidade)
JANELA = 0.010              # s, janela de análise
GUARDA = 0.002              # s, descartado na recepção (eco/reverberação)
SIMBOLO = JANELA + GUARDA

PREAMBULO = 0.100
CHIRP_F0, CHIRP_F1 = 800.0, 3500.0
SILENCIO_POS_PREAMBULO = 0.020
SILENCIO_ENTRE_PACOTES = 0.040
AMPLITUDE = 0.5
# Correlação normalizada mínima para aceitar o chirp. Com o passa-banda na recepção, ruído
# puro fica abaixo de ~0.15; e um falso positivo custa só uma tentativa de decodificação,
# porque o sincronizador volta para logo depois do chirp quando o pacote não abre.
LIMIAR_SYNC = 0.25

PAYLOAD = 64
CABECALHO = struct.Struct(">HHHB")   # sessão, seq, total, tamanho útil
NSYM_RS = 16
_rs = RSCodec(NSYM_RS)

BYTES_PACOTE = CABECALHO.size + PAYLOAD + 4 + NSYM_RS
NIBBLES_PACOTE = BYTES_PACOTE * 2
SLOTS_PACOTE = -(-NIBBLES_PACOTE // N_BANCOS)     # símbolos no ar por pacote
MAX_PACOTES = 65535


def amostras(segundos, sr):
    return int(round(segundos * sr))


def frequencias():
    """Todos os tons, em ordem; o banco k usa os 16 a partir de k * N_TONS."""
    return F_BASE + F_PASSO * np.arange(N_TONS * N_BANCOS)


def banda_util():
    """Faixa que o receptor deixa passar: cobre o chirp e os tons, com folga para o filtro."""
    return (min(CHIRP_F0, F_BASE) - 200.0, max(CHIRP_F1, frequencias()[-1]) + 400.0)


def preambulo(sr):
    n = amostras(PREAMBULO, sr)
    t = np.arange(n) / sr
    sinal = chirp(t, f0=CHIRP_F0, f1=CHIRP_F1, t1=PREAMBULO, method="linear")
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
    bytes_ = np.frombuffer(bruto, dtype=np.uint8)
    nibbles = np.zeros(SLOTS_PACOTE * N_BANCOS, dtype=np.int64)
    nibbles[0:2 * len(bytes_):2] = bytes_ >> 4
    nibbles[1:2 * len(bytes_):2] = bytes_ & 0x0F
    n_sim = amostras(SIMBOLO, sr)
    freqs = frequencias()
    dados = np.zeros(SLOTS_PACOTE * n_sim)
    for k in range(N_BANCOS):           # nibble i vai no símbolo i // N_BANCOS, banco i % N_BANCOS
        f_inst = np.repeat(freqs[N_TONS * k + nibbles[k::N_BANCOS]], n_sim)
        dados += np.sin(2 * np.pi * np.cumsum(f_inst) / sr)     # fase contínua: sem estalos
    dados *= AMPLITUDE / np.sqrt(N_BANCOS)                      # potência total de um tom só
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
    blocos = trecho[: SLOTS_PACOTE * n_sim].reshape(SLOTS_PACOTE, n_sim)[:, n_sim - n_jan:]
    espectro = np.abs(np.fft.rfft(blocos, axis=1))
    bins = np.round(frequencias() / (sr / n_jan)).astype(int)
    mag = espectro[:, bins].reshape(SLOTS_PACOTE, N_BANCOS, N_TONS)
    nibbles = np.argmax(mag, axis=2).reshape(-1)[:NIBBLES_PACOTE]
    return bytes(((nibbles[0::2] << 4) | nibbles[1::2]).astype(np.uint8))


# ---------------------------------------------------------------- sincronismo

class Sincronizador:
    """Recebe áudio em blocos, acha o chirp e entrega os bytes brutos de cada pacote.

    `ao_pacote(bruto)` deve devolver True se o pacote decodificou. Quando não decodifica, a
    busca recomeça logo depois do chirp em vez de pular o pacote inteiro: um falso positivo
    do chirp não engole um pacote de verdade que esteja logo adiante.
    """

    def __init__(self, sr, ao_pacote):
        self.sr = sr
        self.ao_pacote = ao_pacote
        self.pre = preambulo(sr)
        self.L = len(self.pre)
        self.norma_pre = float(np.linalg.norm(self.pre))
        self.n_gap = amostras(SILENCIO_POS_PREAMBULO, sr)
        self.n_dados = SLOTS_PACOTE * amostras(SIMBOLO, sr)
        self.busca = amostras(0.5, sr)
        # FIR de fase linear: atrasa chirp e dados igualmente, então os índices continuam
        # alinhados. ~400 taps em 48 kHz dão transição de uns 400 Hz.
        self.fir = firwin(int(sr / 120) | 1, banda_util(), pass_zero=False, fs=sr)
        self.zi = np.zeros(len(self.fir) - 1)
        self.buf = np.zeros(0, dtype=np.float32)

    def alimentar(self, x):
        x, self.zi = lfilter(self.fir, [1.0], np.asarray(x, dtype=np.float64), zi=self.zi)
        self.buf = np.concatenate([self.buf, x.astype(np.float32)])
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
            if self.ao_pacote(demodular(self.buf[inicio:fim], self.sr)):
                self.buf = self.buf[fim:]
            else:
                self.buf = self.buf[i + self.L:]         # chirp falso ou pacote perdido
