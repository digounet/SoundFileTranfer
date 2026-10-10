"""
Simula o canal acústico sobre o modem real, sem hardware: ruído branco, cliques e reverberação.

    python simular.py

Serve de teste de regressão para mudanças no modem.py: imprime quantos pacotes chegaram em cada
cenário e termina com código 1 se um cenário de referência ficar abaixo de 100 %.
Sementes fixas, resultado determinístico.
"""
import sys

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


def receber(audio):
    ok, falhas = set(), [0]

    def ao_pacote(bruto):
        r = modem.abrir_pacote(bruto)
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
