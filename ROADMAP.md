# Roadmap

O que ainda falta no HCX para Linux, em ordem de prioridade. A lista vem da
comparação do interpretador com a *HyperTalk Reference 2.4* e dos testes com
pilhas reais. Quer ajudar? Veja [Como ajudar](#como-ajudar) no fim.

*English: this is the list of what HCX for Linux still lacks, by priority.
Pull requests and issues are welcome in English too.*

## Já funciona

- Leitura de pilhas HyperCard 1.x e 2.x, MacBinary, BinHex, AppleDouble e imagens de disco HFS
- Interpretador HyperTalk: cerca de 570 comandos, funções e propriedades da referência 2.4 testados
- Os XCMDs/XFCNs mais comuns reescritos em Python (lista no README)
- Interface em português, espanhol e inglês

## Próxima versão (3.1): pilhas que reagem ao usuário

Hoje a janela só manda `mouseDown` e `mouseUp` para os scripts. Muitas pilhas
dependem das outras mensagens do sistema.

- [ ] Mandar `mouseEnter`, `mouseWithin`, `mouseLeave` e `mouseStillDown` aos botões e campos
- [ ] Mandar `keyDown`, `arrowKey`, `returnKey`, `enterKey`, `tabKey`, `functionKey`, `controlKey` e `commandKeyDown` quando o usuário digita, para os scripts poderem interceptar. Hoje as setas trocam de cartão direto, sem passar pelos scripts.
- [ ] Mandar `openField`, `closeField`, `exitField`, `returnInField` e `enterInField` ao editar um campo
- [ ] `mouseDoubleClick`
- [ ] Caixa de mensagens (Message Box) para digitar comandos HyperTalk, como no Mac (Ctrl+M)
- [ ] `put … before/after menuItem …`
- [ ] `select` (texto, linhas e objetos) e `choose tool` vindos de scripts
- [ ] `flash`, `drag` e `move` (hoje não fazem nada)
- [ ] `play` com notas e tempo (`play "harpsichord" tempo 200 "c4 d e"`); hoje toca só o som

## 3.2: som, cor e fontes

- [ ] Sons comprimidos MACE 3:1, MACE 6:1 e IMA 4:1 (hoje tocam como um bipe)
- [ ] Cores: o XCMD AddColor e os recursos de cor que ele usa
- [ ] Fontes bitmap do Mac guardadas na pilha (recursos FONT/NFNT), para o texto quebrar a linha no mesmo lugar que no Mac
- [ ] Mais XCMDs/XFCNs reescritos, conforme as pilhas que a comunidade testar

## 3.3: arquivos e edição

- [ ] Imagens de disco HFS+ (Mac OS Estendido)
- [ ] Criar e apagar cartões, pelo menu e por script (`doMenu "New Card"`, `delete this card`, `create card`)
- [ ] Recortar, copiar e colar cartões
- [ ] `create stack` e `save stack as`
- [ ] `print field`, `print` de uma expressão e `open report printing` (relatórios com modelo)
- [ ] Editor de scripts (hoje os scripts só podem ser lidos, no inspetor)

## Mais para a frente

- [ ] Ferramentas de pintura (lápis, pincel, formas) e os comandos de script que as usam (`choose brush tool`, `drag`, `the brush`, `the pattern`…)
- [ ] `export paint` e `import paint`
- [ ] Depurador: checkpoints, Message Watcher, Variable Watcher
- [ ] Pacotes para Flatpak e outras distribuições

## Não planejado

- AppleEvents e AppleScript (`request … from program`, `reply`, `send … to program`, `do … as AppleScript`): não existem no Linux. Os comandos são aceitos e não fazem nada.
- XCMDs que dependem do hardware do Mac antigo (`dial` pelo modem, por exemplo)

## Como ajudar

- **Testar pilhas:** abra suas pilhas e conte o que não funcionou numa issue, com o nome da pilha e, se puder, um link para ela.
- **Ver o que falta numa pilha:** rode `HCX_DEBUG=1 ./HCX-x86_64.AppImage pilha.img` no terminal. Cada comando, XCMD ou função que o HCX ainda não conhece aparece como `hcx: unhandled: …` e erros de script como `hcx: script error: …`. Cole essas linhas na issue.
- **Traduzir:** veja [TRANSLATING.md](TRANSLATING.md).
- **Programar:** escolha um item desta lista e mande um pull request. O interpretador fica em `hcx/hypertalk.py`, a ligação com a janela em `hcx/hthost.py` e os XCMDs em `hcx/xcmds.py`.
