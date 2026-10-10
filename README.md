# SoundFileTranfer

Transfere uma pasta entre dois computadores usando apenas som: o emissor toca os dados no alto-falante e o receptor escuta pelo microfone. Não precisa de rede, cabo nem pareamento. É lento (dezenas de bytes por segundo), então serve para pastas pequenas: configurações, chaves, scripts, textos.

## Como funciona

1. `enviar.py` compacta a pasta em um ZIP (LZMA) em memória.
2. O ZIP é fatiado em pacotes de 192 bytes. Cada pacote recebe cabeçalho (sessão, sequência, total, tamanho útil), CRC32 e 48 bytes de Reed-Solomon, somando 251 bytes.
3. Cada byte vira dois nibbles de 4 bits. A cada símbolo de 12 ms (10 ms de análise e 2 ms de guarda contra eco) tocam dois tons ao mesmo tempo, um de cada banco de 16 tons: o banco 1 ocupa 1000 a 2500 Hz, o banco 2 ocupa 2600 a 4100 Hz. Um símbolo carrega um byte. Os tons são múltiplos de 100 Hz, ortogonais na janela de 10 ms.
4. Antes de cada pacote vai um chirp de 100 ms (800 → 3500 Hz) que o receptor usa para se sincronizar.
5. `receber.py` filtra o áudio na faixa útil (600 a 4500 Hz), acha o chirp por correlação, demodula os tons com FFT, corrige até 24 bytes errados por pacote com Reed-Solomon e descarta pelo CRC o que não deu para corrigir. Quando todos os pacotes chegam, extrai o ZIP na pasta de destino.

A sessão é o CRC do ZIP, então rodar o emissor de novo com a mesma pasta continua a mesma recepção. Se faltou algo, o receptor imprime quais pacotes faltam e o emissor reenvia só esses com `--pacotes`.

Um pacote no ar:

```
[chirp 100 ms][silêncio 20 ms][251 símbolos × 12 ms][silêncio 40 ms]   = 3,17 s
```

| Campo                                        | Bytes |
|----------------------------------------------|------:|
| Cabeçalho (sessão, seq, total, tamanho útil) |     7 |
| Payload                                      |   192 |
| CRC32                                        |     4 |
| Reed-Solomon (corrige até 24 bytes)          |    48 |
| Total                                        |   251 |

## Instalar

Precisa de Python 3. As dependências (`numpy`, `scipy`, `reedsolo`, `sounddevice`) vêm como pacote pronto do pip: sem compilador e sem administrador. No Windows e no macOS o `sounddevice` já traz o PortAudio embutido; só no Linux a biblioteca do sistema precisa ser instalada.

macOS e Linux:

```bash
sudo apt install libportaudio2    # só no Linux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell ou cmd, sem administrador):

```powershell
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Para só gravar ou ler WAV (`--wav`), o `sounddevice` não é necessário: os dois scripts só o importam na hora de tocar ou escutar.

## Uso

Primeiro, no computador que vai receber:

```bash
python receber.py ./recebidos
```

Depois, no computador que vai enviar:

```bash
python enviar.py ./minha_pasta
python enviar.py ./minha_pasta --repetir 2    # ambiente ruidoso: manda tudo duas vezes
```

O emissor imprime a sessão e a estimativa de tempo antes de começar. O receptor mostra o progresso, extrai a pasta quando completa e encerra sozinho.

Se a transmissão acabou e faltaram pacotes, Ctrl+C no receptor mostra o que falta e o comando para completar:

```
Incompleto: faltam 2 de 40 pacotes.
Para completar, rode no emissor:  python enviar.py <pasta> --pacotes 7,23
```

Rode esse comando no emissor com o receptor ainda escutando (ou abra o receptor de novo: ele aceita a mesma sessão). O receptor também avisa, a cada volta do emissor, o que ainda falta.

### Testar sem alto-falante e microfone

A transmissão pode ser gravada em WAV e decodificada a partir do arquivo:

```bash
python enviar.py ./minha_pasta --wav tx.wav
python receber.py ./recebidos --wav tx.wav
```

`simular.py` aplica ruído branco, cliques e reverberação ao áudio do modem e conta quantos pacotes chegam em cada cenário. É o teste de regressão para mudanças no `modem.py`: termina com erro se um cenário de referência ficar abaixo de 100 %.

```bash
python simular.py
```

## Dicas

- Deixe os dispositivos a menos de 1 m um do outro, com volume médio-alto, sem distorcer.
- Reverberação (sala vazia, azulejo) e distância atrapalham mais que ruído de fundo contínuo.
- Não precisa `--repetir 2` por padrão: é mais barato mandar uma vez e reenviar só o que faltou.
- Se a pasta mudou entre um envio e outro, a sessão muda e o receptor recomeça do zero. É o esperado: o conteúdo é outro.

## Velocidade e limites

| Medida            | Valor                                         |
|-------------------|-----------------------------------------------|
| Taxa útil         | ~60 B/s (192 bytes a cada 3,17 s)             |
| 1 KB compactado   | ~17 s                                         |
| 100 KB compactado | ~28 min                                       |
| Tamanho máximo    | 65 535 pacotes, ~12 MB compactados            |
| Faixa de áudio    | 800 a 4100 Hz, dentro do alcance de qualquer alto-falante e microfone de notebook |

## Decisoes

A primeira versão usava um tom por vez (1000 a 2500 Hz), pacotes de 64 bytes, sessão sorteada e duas voltas por padrão: ~27 B/s úteis. A simulação mostrou que o elo fraco era o sincronismo, não os símbolos: o chirp deixava de ser detectado com ruído a −3 dB enquanto os símbolos aguentavam −12 dB, e qualquer clique em cima do chirp perdia o pacote inteiro. Isso definiu a ordem das mudanças.

> **Revisão (2026-10-10):** quatro mudanças, cada uma validada pela simulação.
> 1. Passa-banda na recepção, limiar do chirp de 0,40 para 0,25 e, quando o pacote não abre, a busca volta para logo depois do chirp em vez de pular o pacote. Ruído tolerado foi de −3 dB para −12 dB; cliques deixaram de perder pacotes. Custo zero em tempo.
> 2. Dois bancos de tons em paralelo, mesma potência total. Taxa de 27 para 51 B/s ao custo de 3 dB, que o item 1 repôs com folga.
> 3. Sessão derivada do CRC do ZIP e `--pacotes` para reenviar só o que faltou. Permitiu `--repetir 1` por padrão.
> 4. Pacotes de 192 bytes com 48 de Reed-Solomon: mesma fração corrigível, menos tempo gasto com chirp, silêncios e cabeçalho. Taxa de 51 para 60 B/s.
>
> Descartado: decodificação com apagamentos (sem ganho em nenhum cenário). Quatro bancos ou janela de 5 ms chegam a 80 a 88 B/s na simulação, mas usam até 7300 Hz e tons simultâneos demais para alto-falante barato; só com teste real.

## Arquivos

- `modem.py`: modulação, demodulação, pacotes e sincronismo. Compartilhado pelos dois lados.
- `enviar.py`: emissor (alto-falante ou WAV).
- `receber.py`: receptor (microfone ou WAV).
- `simular.py`: canal simulado para teste de regressão do modem.
