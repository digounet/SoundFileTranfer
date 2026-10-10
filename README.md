# SoundFileTranfer

Transfere uma pasta entre dois computadores usando apenas som: o emissor toca os dados no alto-falante e o receptor escuta pelo microfone. Não precisa de rede, cabo nem pareamento. É lento (dezenas de bytes por segundo), então serve para pastas pequenas: configurações, chaves, scripts, textos.

## Como funciona

1. `enviar.py` compacta a pasta em um ZIP (LZMA) em memória.
2. O ZIP é fatiado em pacotes de 160 bytes. Cada pacote recebe cabeçalho (sessão, sequência, total, tamanho útil), CRC32 e 80 bytes de Reed-Solomon, somando 251 bytes.
3. Cada byte vira dois nibbles de 4 bits. A cada símbolo de 16 ms (10 ms de análise e 6 ms de guarda contra eco) toca um de 16 tons, de 2600 a 4100 Hz. Um símbolo carrega meio byte. Os tons são múltiplos de 100 Hz, ortogonais na janela de 10 ms.
4. Antes de cada pacote vai um chirp de 100 ms (800 → 3500 Hz) que o receptor usa para se sincronizar.
5. `receber.py` filtra o áudio na faixa útil (600 a 4500 Hz), acha o chirp por correlação e demodula os tons com FFT. Antes de decidir cada tom, desconta um quarto do que o tom do símbolo anterior media, porque em sala reverberante ele ainda soa dentro da janela seguinte. O Reed-Solomon corrige até 40 bytes errados por pacote e o CRC descarta o que não deu para corrigir. Quando um pacote não abre, o receptor guarda as magnitudes dos tons; se outra cópia corrompida do mesmo pacote chegar (reconhecida por concordar em 10 % dos bytes), soma as duas antes de decidir de novo. Quando todos os pacotes chegam, extrai o ZIP na pasta de destino.

A sessão é o CRC do ZIP, então rodar o emissor de novo com a mesma pasta continua a mesma recepção. Se faltou algo, o receptor imprime quais pacotes faltam e o emissor reenvia só esses com `--pacotes`.

Um pacote no ar:

```
[chirp 100 ms][silêncio 20 ms][502 símbolos × 16 ms][silêncio 40 ms]   = 8,19 s
```

| Campo                                        | Bytes |
|----------------------------------------------|------:|
| Cabeçalho (sessão, seq, total, tamanho útil) |     7 |
| Payload                                      |   160 |
| CRC32                                        |     4 |
| Reed-Solomon (corrige até 40 bytes)          |    80 |
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

Não precisa nem do Ctrl+C: depois de uns 13 s sem pacotes o receptor imprime isso sozinho e segue escutando. O que já chegou fica guardado em `<destino>/.parcial.json`, então tanto faz deixar o receptor aberto ou fechá-lo e abrir de novo com a mesma pasta: ele retoma de onde parou. O arquivo é apagado quando a pasta é extraída.

### Testar sem alto-falante e microfone

A transmissão pode ser gravada em WAV e decodificada a partir do arquivo:

```bash
python enviar.py ./minha_pasta --wav tx.wav
python receber.py ./recebidos --wav tx.wav
```

`simular.py` aplica ruído branco, cliques, reverberação sintética e a resposta impulsiva medida de um canal real (`canais/pc_mac_30cm.wav`) ao áudio do modem e conta quantos pacotes chegam em cada cenário. É o teste de regressão para mudanças no `modem.py`: termina com erro se um cenário de referência ficar abaixo de 100 %.

```bash
python simular.py
```

## Dicas

- Aponte o alto-falante do emissor para o microfone do receptor, a uns 10 a 30 cm. Alto-falante de notebook costuma ficar embaixo ou atrás: aí o som chega ao outro lado por reflexão na mesa, e o eco de cada tom invade o tom seguinte. Foi o que o primeiro teste real mediu: a 30 cm, a energia refletida superava a direta.
- Volume médio-alto, sem distorcer. Reverberação (sala vazia, azulejo) atrapalha mais que ruído de fundo contínuo.
- Não precisa `--repetir 2` por padrão: é mais barato mandar uma vez e reenviar só o que faltou.
- Se a pasta mudou entre um envio e outro, a sessão muda e o receptor recomeça do zero. É o esperado: o conteúdo é outro.

## Velocidade e limites

| Medida            | Valor                                         |
|-------------------|-----------------------------------------------|
| Taxa útil         | ~19,5 B/s (160 bytes a cada 8,19 s)           |
| 1 KB compactado   | ~52 s                                         |
| 100 KB compactado | ~1,4 h                                        |
| Tamanho máximo    | 65 535 pacotes, ~10 MB compactados            |
| Faixa de áudio    | 800 a 4100 Hz (tons em 2600 a 4100 Hz), dentro do alcance de qualquer alto-falante e microfone de notebook |

## Decisoes

A primeira versão usava um tom por vez (1000 a 2500 Hz), pacotes de 64 bytes, sessão sorteada e duas voltas por padrão: ~27 B/s úteis. A simulação mostrou que o elo fraco era o sincronismo, não os símbolos: o chirp deixava de ser detectado com ruído a −3 dB enquanto os símbolos aguentavam −12 dB, e qualquer clique em cima do chirp perdia o pacote inteiro. Isso definiu a ordem das mudanças.

> **Revisão (2026-10-10):** quatro mudanças, cada uma validada pela simulação.
> 1. Passa-banda na recepção, limiar do chirp de 0,40 para 0,25 e, quando o pacote não abre, a busca volta para logo depois do chirp em vez de pular o pacote. Ruído tolerado foi de −3 dB para −12 dB; cliques deixaram de perder pacotes. Custo zero em tempo.
> 2. Dois bancos de tons em paralelo, mesma potência total. Taxa de 27 para 51 B/s ao custo de 3 dB, que o item 1 repôs com folga.
> 3. Sessão derivada do CRC do ZIP e `--pacotes` para reenviar só o que faltou. Permitiu `--repetir 1` por padrão.
> 4. Pacotes de 192 bytes com 48 de Reed-Solomon: mesma fração corrigível, menos tempo gasto com chirp, silêncios e cabeçalho. Taxa de 51 para 60 B/s.
>
> Descartado: decodificação com apagamentos (sem ganho em nenhum cenário). Quatro bancos ou janela de 5 ms chegam a 80 a 88 B/s na simulação, mas usam até 7300 Hz e tons simultâneos demais para alto-falante barato; só com teste real.

> **Revisão (2026-10-10, após o primeiro teste real):** notebook Windows a 70 % de volume, 30 cm de um MacBook, sala de escritório: chegaram 1 a 3 pacotes de 17. A gravação mostrou SNR de 25 dB e sincronismo correto; o problema era eco. A resposta impulsiva medida tem a energia refletida acima da direta, e com 2 ms de guarda o tom anterior ainda tinha 60 % da amplitude na janela seguinte, vencendo os tons que o alto-falante reproduz fraco (até −16 dB em 3400 Hz). Correções, escolhidas emulando esse canal na simulação: guarda de 2 para 6 ms, Reed-Solomon de 48 para 80 bytes (payload 160) e desconto de 25 % do rastro do tom anterior antes de decidir. Taxa de 60 para 38 B/s; no canal medido, de 4 de 32 pacotes para 100 %, e 97 % com a reverberação 3 e 6 dB pior. Testado e descartado: equalização por tom, estimada das decisões ou do chirp, piorou em todos os casos; guarda acima de 6 ms não ajuda, porque a cauda difusa domina; Reed-Solomon de 64 bytes (42 B/s) perde margem nos canais piores.

> **Revisão (2026-10-10, segundo teste real):** com o modem acima, 21 pacotes exigiram várias rodadas: 10 a 15 % dos símbolos saíam errados, perto do limite de 40 bytes do Reed-Solomon, em ambiente silencioso e com sincronismo correto. Os erros caem em posições diferentes a cada cópia, então o receptor passou a somar as magnitudes das cópias corrompidas. Na gravação real isso fechou os pacotes 17 e 19 já na segunda cópia, uma volta antes. Na simulação, onde o eco é idêntico em toda volta, não há ganho. O erro é pior no banco de 1000 a 2500 Hz (12 %) que no de 2600 a 4100 Hz (3 %), possivelmente por reverberação mais longa nas frequências baixas; isso ainda não foi atacado.

> **Revisão (2026-10-10, terceiro teste real):** quase todo pacote falhava. Os 3 canais medidos (`canais/`) foram reemulados e agora reproduzem o real: o modem de dois bancos entregava de 0 a 19 de 30 pacotes. Descartados: deriva de relógio (erro não cresce ao longo do pacote, alinhamento ótimo em 0 ms) e distorção (−21 dB fora dos tons). A causa é eco, pior nas frequências baixas: o banco de 1000 a 2500 Hz errava 10 a 17 % dos símbolos, o de 2600 a 4100 Hz errava 4 %. Voltei a um banco só, em 2600 a 4100 Hz, com o mesmo pacote de 160 B e Reed-Solomon de 80: erro de 4 % contra 16 % de limite, 30 de 30 nos canais medidos, e −12 dB de ruído, que antes não passava, também passa. Taxa de 38 para 19,5 B/s. Alternativas que passaram nos dois canais mas com menos folga: dois bancos com Reed-Solomon de 140 (23,9 B/s) e um banco com pacote de 192 (23,4 B/s). Guarda menor, janela de 20 ms e janela de 8 ms foram piores. Ainda não testado: tons acima de 4200 Hz, onde a resposta do canal não foi medida.

## Arquivos

- `modem.py`: modulação, demodulação, pacotes e sincronismo. Compartilhado pelos dois lados.
- `enviar.py`: emissor (alto-falante ou WAV).
- `receber.py`: receptor (microfone ou WAV).
- `simular.py`: canal simulado para teste de regressão do modem.
- `canais/`: respostas impulsivas medidas em canais reais, usadas pela simulação.
