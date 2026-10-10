"""
Receptor: escuta o microfone (ou lê um WAV), remonta os pacotes e extrai a pasta.

    python receber.py ./recebidos
    python receber.py ./recebidos --wav transmissao.wav

Se a transmissão terminar incompleta, imprime quais pacotes faltam no formato que
`enviar.py --pacotes` aceita. O emissor pode ser rodado de novo com a mesma pasta: a sessão
é a mesma e o receptor só completa o que falta.
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
        self.ultimo_seq = -1
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
        if sessao != self.sessao:                    # outra transmissão: recomeça
            self.sessao, self.total, self.partes = sessao, total, {}
            self.ultimo_seq = -1
            print(f"\nsessão {sessao:04x}, {total} pacotes")
        elif seq <= self.ultimo_seq and len(self.partes) < self.total:
            print(f"\nvolta terminou; faltam: {self.faltando()}")   # emissor voltou ao início
        self.ultimo_seq = seq
        self.partes[seq] = dados
        self._status()
        if len(self.partes) == self.total:
            self._finalizar()
        return True

    def faltando(self):
        """Pacotes ausentes no formato que enviar.py --pacotes aceita: '3,7-9'."""
        grupos = []
        for seq in range(self.total or 0):
            if seq in self.partes:
                continue
            if grupos and seq == grupos[-1][1] + 1:
                grupos[-1][1] = seq
            else:
                grupos.append([seq, seq])
        return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in grupos)

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

    def despedida(self):
        if self.concluido:
            return
        if not self.total:
            print("\nNenhum pacote reconhecido.")
            return
        print(f"\nIncompleto: faltam {self.total - len(self.partes)} de {self.total} pacotes.")
        print(f"Para completar, rode no emissor:  python enviar.py <pasta> --pacotes {self.faltando()}")


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
        montador.despedida()
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
            montador.despedida()


if __name__ == "__main__":
    main()
