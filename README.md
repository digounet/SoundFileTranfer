# SoundFileTranfer

Transfere uma pasta entre dois computadores usando apenas som: o emissor toca os dados no alto-falante e o receptor escuta pelo microfone. Não precisa de rede, cabo nem pareamento. É lento (dezenas de bytes por segundo), então serve para pastas pequenas: configurações, chaves, scripts, textos.

## Como funciona

1. `enviar.py` compacta a pasta em um ZIP (LZMA) em memória.
2. O ZIP é fatiado em pacotes de 64 bytes. Cada pacote recebe cabeçalho (sessão, sequência, total, tamanho útil), CRC32 e 16 bytes de Reed-Solomon, somando 91 bytes.
3. Cada byte vira dois símbolos de 4 bits. Cada símbolo é um de 16 tons entre 1000 e 2500 Hz (MFSK), com 12 ms de duração: 10 ms de análise e 2 ms de guarda contra eco.
4. Antes de cada pacote vai um chirp de 100 ms (800 → 3500 Hz) que o receptor usa para se sincronizar.
5. `receber.py` acha o chirp por correlação, demodula os tons com FFT, corrige até 8 bytes errados por pacote com Reed-Solomon e descarta pelo CRC o que não deu para corrigir. Quando todos os pacotes chegam, extrai o ZIP na pasta de destino.

Por padrão o emissor transmite tudo duas vezes (`--repetir 2`). O receptor completa na segunda volta o que perdeu na primeira e para assim que tiver todos os pacotes.

Um pacote no ar:

```
[chirp 100 ms][silêncio 20 ms][182 símbolos × 12 ms][silêncio 40 ms]   = 2,34 s
```

| Campo                                        | Bytes |
|----------------------------------------------|------:|
| Cabeçalho (sessão, seq, total, tamanho útil) |     7 |
| Payload                                      |    64 |
| CRC32                                        |     4 |
| Reed-Solomon (corrige até 8 bytes)           |    16 |
| Total                                        |    91 |

## Instalar

Precisa de Python 3 e do PortAudio, usado pelo `sounddevice`.

```bash
brew install portaudio            # macOS. Linux: apt install libportaudio2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Uso

Primeiro, no computador que vai receber:

```bash
python receber.py ./recebidos
```

Depois, no computador que vai enviar:

```bash
python enviar.py ./minha_pasta
python enviar.py ./minha_pasta --repetir 3    # ambiente ruidoso
```

O emissor imprime a estimativa de tempo antes de começar. O receptor mostra o progresso, extrai a pasta quando completa e encerra sozinho. Ctrl+C para desistir.

### Testar sem alto-falante e microfone

A transmissão pode ser gravada em WAV e decodificada a partir do arquivo. É o jeito mais rápido de testar mudanças no modem:

```bash
python enviar.py ./minha_pasta --repetir 1 --wav tx.wav
python receber.py ./recebidos --wav tx.wav
```

## Dicas

- Deixe os dispositivos a menos de 1 m um do outro, com volume médio-alto, sem distorcer.
- Reverberação (sala vazia, azulejo) e distância atrapalham mais que ruído de fundo contínuo.
- Cada execução do emissor gera uma sessão nova, e o receptor zera o progresso ao ouvir outra sessão. Se faltaram pacotes, aumente `--repetir` em vez de rodar o emissor de novo.

## Velocidade e limites

| Medida            | Valor                                        |
|-------------------|----------------------------------------------|
| Taxa útil         | ~27 B/s por volta (64 bytes a cada 2,34 s)   |
| 1 KB compactado   | ~40 s por volta                              |
| 100 KB compactado | ~1 h por volta                               |
| Tamanho máximo    | 65 535 pacotes, ~4 MB compactados            |
| Faixa de áudio    | 800 a 3500 Hz, dentro do alcance de qualquer alto-falante e microfone de notebook |

## Proximos passos

Resultados de simulação (ruído branco, cliques e reverberação sobre o WAV gerado), em ordem de ganho por esforço:

1. **Sincronismo.** Hoje é o elo fraco: os símbolos sobrevivem a ruído até −12 dB, mas o chirp deixa de ser detectado em −3 dB, e um clique em cima do chirp perde o pacote inteiro. Filtrar o áudio em 700–3700 Hz antes da correlação, baixar `LIMIAR_SYNC` para 0,25 e, quando o pacote não decodificar, voltar para logo depois do chirp em vez de pular o pacote todo. Custo zero em tempo de transmissão.
2. **Dois bancos de 16 tons em paralelo** (1000–4100 Hz, um nibble por banco a cada símbolo). Dobra a taxa (27 → 51 B/s) custando 3 dB de margem nos símbolos, que o item 1 repõe com folga. Depende do item 1.
3. **Sessão derivada do conteúdo** (CRC do ZIP) e opção de reenviar só os pacotes que faltaram. Permite `--repetir 1` por padrão e completar uma recepção parcial sem recomeçar do zero.
4. **Pacotes maiores** (ex. 192 B de payload com 48 B de Reed-Solomon): mais 10 a 15 % de taxa, com a mesma fração de correção.

## Arquivos

- `modem.py`: modulação, demodulação, pacotes e sincronismo. Compartilhado pelos dois lados.
- `enviar.py`: emissor (alto-falante ou WAV).
- `receber.py`: receptor (microfone ou WAV).
