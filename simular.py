"""
Simula o canal acústico sobre o modem real, sem hardware: ruído branco, cliques, reverberação
sintética e a resposta impulsiva medida de um canal real (canais/pc_mac_30cm.wav: notebook
Windows a 70 % de volume, 30 cm de um MacBook, sala de escritório).

    python simular.py

Serve de teste de regressão para mudanças no modem.py: imprime quantos pacotes chegaram em cada
cenário e termina com código 1 se um cenário de referência ficar abaixo de 100 %.
Sementes fixas, resultado determinístico.
"""
import os
import sys
import wave

import numpy as np
from scipy.signal import fftconvolve

import modem

SR = modem.SR_PADRAO
P_SINAL = modem.AMPLITUDE ** 2 / 2          # potencia dos tons; referencia para a SNR


def transmitir(n_bytes, semente=1):
    dados = np.random.default_rng(semente).bytes(n_bytes)
    partes = [dados[i:i + modem.PAYLOAD] for i in range(0, len(dados), modem.PAYLOAD)]
    pacotes = [modem.montar_pacote(7, i, len(partes), p) for i, p in enumerate(partes)]
    audio = np.concatenate([np.zeros(SR // 4, np.float32)]
                           + [modem.modular(p, SR) for p in pacotes]
                           + [np.zeros(SR, np.float32)])
    return pacotes, audio


def ruido(audio, snr_db, semente):
    """Ruido branco. A SNR e em banda larga (0 a 24 kHz) em relacao a potencia dos tons."""
    sigma = np.sqrt(P_SINAL / 10 ** (snr_db / 10))
    return (audio + np.random.default_rng(semente).normal(0, sigma, len(audio))).astype(np.float32)


def cliques(audio, por_segundo, semente, dur=0.030, amp=1.0):
    """Estalos de ruido branco em amplitude maxima (porta batendo, teclado)."""
    r = np.random.default_rng(semente)
    y = audio.copy()
    n = int(dur * SR)
    for p in r.integers(0, len(audio) - n, int(len(audio) / SR * por_segundo)):
        y[p:p + n] += (amp * r.normal(0, 1, n)).astype(np.float32)
    return y


def reverb(audio, rt60, drr_db, semente):
    """Caminho direto + cauda exponencial de ruido. DRR = energia direta / reverberante, em dB."""
    r = np.random.default_rng(semente)
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    cauda = r.normal(0, 1, n) * np.exp(-6.9 * t / rt60)
    cauda *= np.sqrt(10 ** (-drr_db / 10) / np.sum(cauda ** 2))
    ir = np.concatenate(([1.0], np.zeros(int(0.001 * SR)), cauda))
    return fftconvolve(audio, ir)[:len(audio)].astype(np.float32)


def canal_real(audio, cauda_db, semente, snr_banda=25.0):
    """Convolui com a resposta impulsiva medida; cauda_db > 0 piora a reverberação alem do medido.
    O ruido branco e dosado pela SNR dentro da faixa util, como medida na gravacao (25 dB)."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "canais", "pc_mac_30cm.wav")
    with wave.open(caminho) as w:
        h = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64)
    h[int(0.002 * SR):] *= 10 ** (cauda_db / 20)
    y = fftconvolve(audio.astype(np.float64), h)[:len(audio)]
    y *= np.sqrt(np.mean(audio[audio != 0] ** 2) / np.mean(y[audio != 0] ** 2))   # mesma potencia
    snr_larga = snr_banda - 10 * np.log10(SR / 2 / (modem.banda_util()[1] - modem.banda_util()[0]))
    return ruido(y.astype(np.float32), snr_larga, semente)


def receber(audio):
    ok, falhas = set(), [0]
    acumulador = modem.Acumulador()

    def ao_pacote(bruto, mag):
        r = modem.abrir_pacote(bruto)
        if r is None:
            r = acumulador.tentar(bruto, mag)
        if r is None:
            falhas[0] += 1
            return False
        ok.add(r[1])
        return True

    sync = modem.Sincronizador(SR, ao_pacote)
    for i in range(0, len(audio), 4096):
        sync.alimentar(audio[i:i + 4096])
    sync.alimentar(np.zeros(SR, np.float32))
    return len(ok), falhas[0]


# (nome, canal, exigido): os exigidos precisam chegar a 100 % ou o teste falha
CENARIOS = [
    ("limpo",              lambda a: a,                                      True),
    ("ruido 0 dB",         lambda a: ruido(a, 0, 10),                        True),
    ("ruido -6 dB",        lambda a: ruido(a, -6, 11),                       True),
    ("ruido -9 dB",        lambda a: ruido(a, -9, 12),                       False),
    ("ruido -12 dB",       lambda a: ruido(a, -12, 13),                      False),
    ("4 cliques/s + 0 dB", lambda a: cliques(ruido(a, 0, 14), 4, 15),        True),
    ("reverb DRR 8 dB",    lambda a: ruido(reverb(a, 0.4, 8, 16), 10, 17),   True),
    ("reverb DRR 5 dB",    lambda a: ruido(reverb(a, 0.4, 5, 18), 10, 19),   True),
    ("reverb DRR 2 dB",    lambda a: ruido(reverb(a, 0.4, 2, 20), 10, 21),   False),
    ("canal real 30 cm",    lambda a: canal_real(a, 0, 22),                   True),
    ("canal real +3 dB rev", lambda a: canal_real(a, 3, 23),                  False),
    ("canal real +6 dB rev", lambda a: canal_real(a, 6, 24),                  False),
    # Informativo. Aqui o eco e identico em toda volta, entao os mesmos pacotes falham de novo
    # e a soma de copias nao ajuda; na gravacao real os erros variaram entre copias e ajudou.
    ("canal +6 dB, 2 voltas", lambda a: np.concatenate([canal_real(a, 6, 25), canal_real(a, 6, 26)]), False),
]


def main():
    pacotes, audio = transmitir(12000)
    seg = len(modem.modular(pacotes[0], SR)) / SR
    print(f"{len(pacotes)} pacotes de {modem.PAYLOAD} B ({modem.BYTES_PACOTE} B no ar, "
          f"{seg:.2f} s cada): {modem.PAYLOAD / seg:.1f} B/s")
    print(f"{'cenario':20} {'ok':>7} {'falhas':>7}")
    regressao = False
    for nome, canal, exigido in CENARIOS:
        ok, falhas = receber(canal(audio))
        marca = "  <- REGRESSAO" if exigido and ok < len(pacotes) else ""
        regressao |= bool(marca)
        print(f"{nome:20} {ok:3}/{len(pacotes):<3} {falhas:7}{marca}")
    sys.exit(1 if regressao else 0)


if __name__ == "__main__":
    main()
