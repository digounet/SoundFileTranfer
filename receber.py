"""
Receptor: escuta o microfone (ou lê um WAV), remonta os pacotes e extrai a pasta.

    python receber.py ./recebidos
    python receber.py ./recebidos --wav transmissao.wav

Se a transmissão terminar incompleta, imprime quais pacotes faltam no formato que
`enviar.py --pacotes` aceita. O emissor pode ser rodado de novo com a mesma pasta: a sessão
é a mesma e o receptor só completa o que falta. O que já chegou fica em <destino>/.parcial.json,
então o receptor pode ser fechado e aberto de novo que retoma de onde parou.
"""
import argparse
import base64
import io
import json
import os
import queue
import sys
import time
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
        self.ultimo_pacote_em = None
        self.arquivo_parcial = os.path.join(destino, ".parcial.json")
        self._carregar()

    def _carregar(self):
        """Retoma uma recepção interrompida na mesma pasta de destino."""
        try:
            with open(self.arquivo_parcial) as f:
                p = json.load(f)
        except (OSError, ValueError):
            return
        self.sessao, self.total = p["sessao"], p["total"]
        self.partes = {int(k): base64.b64decode(v) for k, v in p["partes"].items()}
        print(f"Retomando sessão {self.sessao:04x}: {len(self.partes)}/{self.total} pacotes "
              f"já recebidos; faltam: {self.faltando()}")

    def _salvar(self):
        os.makedirs(self.destino, exist_ok=True)
        tmp = self.arquivo_parcial + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"sessao": self.sessao, "total": self.total,
                       "partes": {str(k): base64.b64encode(v).decode() for k, v in self.partes.items()}}, f)
        os.replace(tmp, self.arquivo_parcial)        # troca atômica: Ctrl+C no meio não corrompe

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
        self.ultimo_pacote_em = time.monotonic()
        self._salvar()
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
        try:
            os.remove(self.arquivo_parcial)
        except OSError:
            pass

    def parado_ha(self):
        """Segundos desde o último pacote bom, ou 0 se ainda não chegou nenhum."""
        return time.monotonic() - self.ultimo_pacote_em if self.ultimo_pacote_em else 0.0

    def pedir_faltantes(self):
        print(f"\nFaltam {self.total - len(self.partes)} de {self.total} pacotes: {self.faltando()}")
        print(f"Para completar, rode no emissor:  python enviar.py <pasta> --pacotes {self.faltando()}")

    def despedida(self):
        if self.concluido:
            return
        if not self.total:
            print("\nNenhum pacote reconhecido.")
            return
        self.pedir_faltantes()
        print(f"O que já chegou está guardado em {self.arquivo_parcial}; "
              f"abra o receptor de novo com a mesma pasta que ele continua de onde parou.")


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

    # Depois de uns 3 pacotes de silêncio, avisa o que falta sem esperar Ctrl+C, e segue escutando.
    seg_pacote = (modem.PREAMBULO + modem.SILENCIO_POS_PREAMBULO
                  + modem.SLOTS_PACOTE * modem.SIMBOLO + modem.SILENCIO_ENTRE_PACOTES)
    avisado = False
    print("Escutando... (Ctrl+C para sair)")
    with sd.InputStream(samplerate=args.sr, channels=1, dtype="float32",
                        blocksize=4096, callback=callback):
        try:
            while not montador.concluido:
                sync.alimentar(fila.get())
                parado = montador.parado_ha() > 3 * seg_pacote
                if parado and not avisado and montador.total:
                    montador.pedir_faltantes()
                    print("(continuo escutando)")
                avisado = parado
        except KeyboardInterrupt:
            print("\nInterrompido.")
            montador.despedida()


if __name__ == "__main__":
    main()
