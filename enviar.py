"""
Emissor: compacta uma pasta e transmite pelo alto-falante (ou grava em WAV).

    python enviar.py ./minha_pasta
    python enviar.py ./minha_pasta --repetir 2
    python enviar.py ./minha_pasta --pacotes 3,7-9      # só o que o receptor disse que faltou
    python enviar.py ./minha_pasta --wav transmissao.wav
"""
import argparse
import io
import lzma
import os
import sys
import tarfile
import wave
import zlib

import numpy as np

import modem


IGNORAR = {".DS_Store", "bin", "obj", ".git"}


def _anonimo(info):
    # dono e data fora do arquivo: a mesma pasta gera os mesmos bytes e, portanto, a mesma sessao
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info


def compactar(pasta):
    # tar + xz em um fluxo so: o xz enxerga a repeticao entre arquivos, o ZIP comprime cada um isolado
    mem = io.BytesIO()
    with tarfile.open(fileobj=mem, mode="w:xz", preset=9 | lzma.PRESET_EXTREME) as t:
        for raiz, dirs, arquivos in os.walk(pasta):
            dirs[:] = sorted(d for d in dirs if d not in IGNORAR)
            for nome in sorted(arquivos):
                if nome in IGNORAR:
                    continue
                caminho = os.path.join(raiz, nome)
                t.add(caminho, os.path.relpath(caminho, pasta), filter=_anonimo)
    return mem.getvalue()


def gerar_pacotes(dados):
    partes = [dados[i:i + modem.PAYLOAD] for i in range(0, len(dados), modem.PAYLOAD)]
    if len(partes) > modem.MAX_PACOTES:
        sys.exit(f"Pasta grande demais ({len(dados)} bytes compactados). "
                 f"Limite ~{modem.MAX_PACOTES * modem.PAYLOAD / 1e6:.0f} MB.")
    # A sessão vem do conteúdo, não de um sorteio: rodar de novo com a mesma pasta gera a
    # mesma sessão, e o receptor completa o que faltava em vez de recomeçar do zero.
    sessao = zlib.crc32(dados) & 0xFFFF
    return sessao, [modem.montar_pacote(sessao, i, len(partes), p) for i, p in enumerate(partes)]


def interpretar_lista(texto, total):
    """'3,7-9' -> [3, 7, 8, 9]; é o formato que receber.py imprime quando falta algo."""
    seqs = set()
    try:
        for parte in texto.split(","):
            if parte.strip():
                a, _, b = parte.partition("-")
                seqs.update(range(int(a), int(b or a) + 1))
    except ValueError:
        sys.exit(f"Lista de pacotes inválida: {texto!r}. Use algo como 3,7-9.")
    fora = sorted(s for s in seqs if s >= total)
    if fora:
        sys.exit(f"Pacotes fora da faixa 0-{total - 1}: {fora}")
    return sorted(seqs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pasta")
    ap.add_argument("--repetir", type=int, default=1,
                    help="quantas voltas transmitir; se faltar algo, o receptor diz o que reenviar")
    ap.add_argument("--pacotes", help="só estes pacotes, ex. 3,7-9 (o que o receptor pediu)")
    ap.add_argument("--wav", help="gravar em arquivo WAV em vez de tocar")
    ap.add_argument("--sr", type=int, default=modem.SR_PADRAO)
    args = ap.parse_args()

    dados = compactar(args.pasta)
    sessao, pacotes = gerar_pacotes(dados)
    escolhidos = interpretar_lista(args.pacotes, len(pacotes)) if args.pacotes else range(len(pacotes))
    seg_pacote = len(modem.modular(pacotes[0], args.sr)) / args.sr
    total_s = seg_pacote * len(escolhidos) * args.repetir
    print(f"sessão {sessao:04x}: {len(dados)} bytes compactados, {len(pacotes)} pacotes"
          f"{f', enviando {len(escolhidos)}' if args.pacotes else ''}, "
          f"{args.repetir} volta(s), ~{total_s / 60:.1f} min "
          f"(~{modem.PAYLOAD / seg_pacote:.0f} B/s)")

    def audio():
        yield np.zeros(args.sr // 2, dtype=np.float32)        # aquece o dispositivo
        for volta in range(args.repetir):
            for i in escolhidos:
                print(f"\rvolta {volta + 1}/{args.repetir}  pacote {i + 1}/{len(pacotes)}",
                      end="", flush=True)
                yield modem.modular(pacotes[i], args.sr)
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
