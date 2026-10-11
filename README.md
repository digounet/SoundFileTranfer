# SoundFileTranfer

Transfere uma pasta entre dois computadores usando apenas som: o emissor toca os dados no alto-falante e o receptor escuta pelo microfone. Não precisa de rede, cabo nem pareamento. É lento (dezenas de bytes por segundo), então serve para pastas pequenas: configurações, chaves, scripts, textos.

## Como funciona

```
pasta ─► tar.xz ─► pacotes de 160 B ─► + CRC32 + Reed-Solomon ─► tons de áudio ─► alto-falante
                                                                                          │ ar
pasta ◄─ tar.xz ◄─ pacotes ◄─ corrige erros ◄─ decide o tom de cada símbolo ◄─ chirp ◄─ microfone
```

### No emissor (`enviar.py`)

1. Compacta a pasta em um único tar.xz, em memória, sem `.DS_Store`, `bin/`, `obj/` e `.git/`. Um fluxo só deixa o xz aproveitar a repetição entre arquivos.
2. Fatia o arquivo em pedaços de 160 bytes. O máximo é 65 535 pacotes, uns 10 MB compactados.
3. Monta cada pacote de 251 bytes: cabeçalho de 7 bytes (sessão, número do pacote, total, tamanho útil), os 160 bytes de dados, um CRC32 de 4 bytes e 80 bytes de Reed-Solomon.
4. Transforma cada byte em dois nibbles de 4 bits. Cada nibble escolhe um de 16 tons, e cada tom dura 16 ms.
5. Toca, na ordem: um chirp de sincronismo, um silêncio curto, os 502 símbolos e outro silêncio.

A **sessão** é o CRC do tar.xz. Rodar o emissor de novo com a mesma pasta gera a mesma sessão, e o receptor continua a mesma recepção em vez de recomeçar.

### O sinal no ar

```
[chirp 100 ms][silêncio 20 ms][502 símbolos × 16 ms][silêncio 40 ms]   = 8,19 s por pacote
```

| Campo                                        | Bytes |
|----------------------------------------------|------:|
| Cabeçalho (sessão, seq, total, tamanho útil) |     7 |
| Payload                                      |   160 |
| CRC32                                        |     4 |
| Reed-Solomon (corrige até 40 bytes)          |    80 |
| Total                                        |   251 |

- **Modulação MFSK de 16 tons.** Os tons vão de 2600 a 4100 Hz, de 100 em 100 Hz. Cada símbolo leva 4 bits.
- **Janela de 10 ms mais guarda de 6 ms.** O receptor só analisa os últimos 10 ms de cada símbolo. Os 6 ms iniciais ficam de fora porque o eco do tom anterior ainda está soando neles.
- **Ortogonalidade.** Em 10 ms cabe um número inteiro de ciclos de qualquer múltiplo de 100 Hz. Por isso a FFT separa os tons sem vazamento entre eles.
- **Fase contínua.** O gerador troca de tom sem saltar de fase, então não há estalos que espalhem energia pelo espectro.
- **Chirp de 100 ms, de 800 a 3500 Hz.** Ele varre uma faixa larga, então tem um pico de correlação bem definido. Isso dá o início do pacote com precisão de uma amostra.

### No receptor (`receber.py` e `modem.py`)

1. **Filtra.** Um passa-banda de 600 a 4500 Hz tira ruído e estalos fora da faixa dos tons. Sem ele, o chirp deixava de ser achado com ruído a −3 dB. Com ele, o sistema sobrevive a −12 dB.
2. **Acha o chirp.** Correlaciona o áudio com o chirp conhecido, normalizada pela energia local, e aceita picos acima de 0,25. Se o pacote que vem depois não abrir, a busca volta para logo após o chirp. Assim, um falso positivo não engole um pacote de verdade que esteja logo adiante.
3. **Mede os tons.** Para cada símbolo, uma FFT dos 10 ms finais dá a magnitude dos 16 tons.
4. **Decide, descontando o eco.** Em sala reverberante, o tom do símbolo anterior ainda soa dentro da janela do atual e vence tons fracos. Antes de escolher o maior, o receptor desconta 25 % do que o tom anterior mediu no bin dele.
5. **Corrige.** O Reed-Solomon conserta até 40 bytes errados em 251. O CRC32 recusa o que ainda saiu errado.
6. **Soma cópias ruins.** Se o pacote não abre, o receptor guarda as magnitudes dos tons. Quando outra cópia corrompida do mesmo pacote chega (as duas concordam em pelo menos 10 % dos bytes, e pacotes diferentes concordam em 0,4 %), ele soma as magnitudes e decide de novo. Isso ajuda porque os erros caem em posições diferentes a cada cópia.
7. **Monta.** Guarda cada pacote bom em disco, em `<destino>/.parcial.json`, e, com todos os pacotes, extrai o tar.xz e apaga o arquivo parcial.

### Reenvio do que faltou

Depois de uns 13 s sem pacotes novos, o receptor imprime quais faltam, no formato que o emissor aceita:

```
Faltam 2 de 40 pacotes: 7,23
Para completar, rode no emissor:  python enviar.py <pasta> --pacotes 7,23
```

Como o progresso fica em disco, tanto faz deixar o receptor aberto ou fechá-lo e abrir de novo com a mesma pasta.

### Por que esses números

O canal é um alto-falante de notebook e um microfone de notebook em uma sala comum. Medindo esse canal, o problema principal é o eco, e não o ruído: a energia refletida chega a superar a direta, e dura dezenas de milissegundos. O eco é mais longo nas frequências baixas.

- Por isso os tons ficam em 2600 a 4100 Hz. Com um segundo banco em 1000 a 2500 Hz o dobro de dados cabia no ar, mas esse banco errava de 10 a 17 % dos símbolos, perto ou acima do que o Reed-Solomon corrige.
- A guarda é de 6 ms porque, com 2 ms, o tom anterior ainda tinha 60 % da amplitude na janela seguinte. Acima de 6 ms não melhora, porque a cauda difusa passa a dominar.
- O Reed-Solomon de 80 bytes corrige 16 % dos bytes, contra 4 % de erro medido nos tons atuais. A folga cobre variações da sala.

Os detalhes e o que foi testado e descartado estão em "Decisoes", mais abaixo.

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

`simular.py` aplica ruído branco, cliques, reverberação sintética e as respostas impulsivas medidas de três canais reais (`canais/`) ao áudio do modem e conta quantos pacotes chegam em cada cenário. É o teste de regressão para mudanças no `modem.py`: termina com erro se um cenário de referência ficar abaixo de 100 %.

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

> **Revisão (2026-10-10, quarto teste real):** com o formato atual, notebook Windows para MacBook a 30 cm, uma volta só: 21 de 21 pacotes, 0 descartados, pasta extraída idêntica. Antes disso, um primeiro envio não foi reconhecido porque o PC ainda estava no formato anterior. A pista foi o intervalo entre chirps na gravação, 4,2 s em vez de 8,2 s: emissor e receptor precisam estar no mesmo commit.

## Arquivos

- `modem.py`: modulação, demodulação, pacotes e sincronismo. Compartilhado pelos dois lados.
- `enviar.py`: emissor (alto-falante ou WAV).
- `receber.py`: receptor (microfone ou WAV).
- `simular.py`: canal simulado para teste de regressão do modem.
- `canais/`: respostas impulsivas medidas em canais reais, usadas pela simulação.
