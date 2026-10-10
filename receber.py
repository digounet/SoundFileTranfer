"""
Receptor: escuta o microfone (ou lê um WAV), remonta os pacotes e extrai a pasta.

    python receber.py ./recebidos
    python receber.py ./recebidos --wav transmissao.wav
"""
import argparse
import io
import queue
import sys
import wave
import zipfile

import numpy as np

import modem


class Montador:
    def __init__(self, destino):
        self.destino = destino
        self.sessao = None
        self.partes = {}
        self.total = None
        self.ruins = 0
        self.concluido = False

    def ao_pacote(self, bruto):
        """Devolve True se o pacote decodificou; o sincronizador usa isso para saber onde seguir."""
        if self.concluido:
            return True
        r = modem.abrir_pacote(bruto)
        if r is None:
            self.ruins += 1
            self._status()
            return False
        sessao, seq, total, dados = r
        if sessao != self.sessao:                    # nova transmissão: recomeça
            self.sessao, self.total, self.partes = sessao, total, {}
        self.partes[seq] = dados
        self._status()
        if len(self.partes) == self.total:
            self._finalizar()
        return True

    def _status(self):
        if self.total:
            print(f"\r{len(self.partes)}/{self.total} pacotes  "
                  f"(corrompidos descartados: {self.ruins})", end="", flush=True)

    def _finalizar(self):
        dados = b"".join(self.partes[i] for i in range(self.total))
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            ruim = z.testzip()
            if ruim:
                print(f"\nCRC falhou em {ruim}")
                return
            z.extractall(self.destino)
            nomes = z.namelist()
        print(f"\nRecebidos {len(nomes)} arquivo(s) em {self.destino}:")
        for n in nomes:
            print("  ", n)
        self.concluido = True


def ler_wav(caminho):
    with wave.open(caminho, "rb") as w:
        sr, canais, largura = w.getframerate(), w.getnchannels(), w.getsampwidth()
        bruto = w.readframes(w.getnframes())
    if largura != 2:
        sys.exit("Use WAV PCM 16 bits.")
    x = np.frombuffer(bruto, dtype=np.int16).reshape(-1, canais)[:, 0]
    return x.astype(np.float32) / 32768, sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("destino")
    ap.add_argument("--wav", help="decodificar de um arquivo WAV em vez do microfone")
    ap.add_argument("--sr", type=int, default=modem.SR_PADRAO)
    args = ap.parse_args()

    montador = Montador(args.destino)

    if args.wav:
        x, sr = ler_wav(args.wav)
        sync = modem.Sincronizador(sr, montador.ao_pacote)
        for i in range(0, len(x), 8192):
            sync.alimentar(x[i:i + 8192])
            if montador.concluido:
                break
        sync.alimentar(np.zeros(sr, dtype=np.float32))   # empurra o final
        if not montador.concluido:
            print(f"\nIncompleto: faltam {(montador.total or 0) - len(montador.partes)} pacotes.")
        return

    import sounddevice as sd
    fila = queue.Queue()
    sync = modem.Sincronizador(args.sr, montador.ao_pacote)

    def callback(indata, frames, tempo, status):
        fila.put(indata[:, 0].copy())

    print("Escutando... (Ctrl+C para sair)")
    with sd.InputStream(samplerate=args.sr, channels=1, dtype="float32",
                        blocksize=4096, callback=callback):
        try:
            while not montador.concluido:
                sync.alimentar(fila.get())
        except KeyboardInterrupt:
            print("\nInterrompido.")


if __name__ == "__main__":
    main()
