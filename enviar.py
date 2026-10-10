"""
Emissor: compacta uma pasta e transmite pelo alto-falante (ou grava em WAV).

    python enviar.py ./minha_pasta
    python enviar.py ./minha_pasta --repetir 3
    python enviar.py ./minha_pasta --wav transmissao.wav
"""
import argparse
import io
import os
import random
import sys
import wave
import zipfile

import numpy as np

import modem


def compactar(pasta):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_LZMA) as z:
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                caminho = os.path.join(raiz, nome)
                z.write(caminho, os.path.relpath(caminho, pasta))
    return mem.getvalue()


def gerar_pacotes(dados):
    partes = [dados[i:i + modem.PAYLOAD] for i in range(0, len(dados), modem.PAYLOAD)]
    if len(partes) > modem.MAX_PACOTES:
        sys.exit(f"Pasta grande demais ({len(dados)} bytes compactados). Limite ~4 MB.")
    sessao = random.randint(0, 0xFFFF)
    return [modem.montar_pacote(sessao, i, len(partes), p) for i, p in enumerate(partes)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pasta")
    ap.add_argument("--repetir", type=int, default=2,
                    help="quantas voltas transmitir (o receptor completa o que perdeu)")
    ap.add_argument("--wav", help="gravar em arquivo WAV em vez de tocar")
    ap.add_argument("--sr", type=int, default=modem.SR_PADRAO)
    args = ap.parse_args()

    dados = compactar(args.pasta)
    pacotes = gerar_pacotes(dados)
    seg_pacote = len(modem.modular(pacotes[0], args.sr)) / args.sr
    total_s = seg_pacote * len(pacotes) * args.repetir
    print(f"{len(dados)} bytes compactados, {len(pacotes)} pacotes, "
          f"{args.repetir} volta(s), ~{total_s / 60:.1f} min "
          f"(~{modem.PAYLOAD / seg_pacote:.0f} B/s)")

    def audio():
        yield np.zeros(args.sr // 2, dtype=np.float32)        # aquece o dispositivo
        for volta in range(args.repetir):
            for i, p in enumerate(pacotes):
                print(f"\rvolta {volta + 1}/{args.repetir}  pacote {i + 1}/{len(pacotes)}",
                      end="", flush=True)
                yield modem.modular(p, args.sr)
        yield np.zeros(args.sr // 2, dtype=np.float32)

    if args.wav:
        with wave.open(args.wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(args.sr)
            for bloco in audio():
                w.writeframes((np.clip(bloco, -1, 1) * 32767).astype(np.int16).tobytes())
    else:
        import sounddevice as sd
        with sd.OutputStream(samplerate=args.sr, channels=1, dtype="float32") as saida:
            for bloco in audio():
                saida.write(bloco.reshape(-1, 1))
    print("\nTransmissão concluída.")


if __name__ == "__main__":
    main()
