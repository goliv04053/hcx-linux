# tools/ghidra: descompilar XCMDs/XFCNs com o Ghidra

Ferramenta de desenvolvimento, como o `tools/xemu`. **Não faz parte do HCX**
nem do AppImage, e o Ghidra não vem junto: cada um instala o seu. Serve para
ler o código 68k original de um externo como C, com os traps da Toolbox e do
sistema já nomeados e tipados, antes de escrever a versão nativa em
`hcx/xcmds.py`.

## Preparar (uma vez)

1. Baixe e descompacte o Ghidra 11.x (testado com 11.4.2, precisa de Java 21):
   <https://github.com/NationalSecurityAgency/ghidra/releases>
2. Clone as Multiversal Interfaces (descrição de todos os traps e tipos do
   Mac, licença MIT, de Wolfgang Thaller):
   `git clone https://github.com/autc04/multiversal`
3. Rode `sh tools/ghidra/setup.sh PASTA_DO_GHIDRA PASTA_DO_MULTIVERSAL`
   (precisa do PyYAML).

O `setup.sh` aplica dois patches nos arquivos de linguagem 68k do Ghidra
(guardando os originais como `*.orig`) e recompila a especificação do 68020:

- `68020-atrap.patch`: as instruções de linha A (os traps do Mac) viram uma
  chamada para um endereço próprio de cada trap, em vez de "instrução
  inválida". Traps do sistema devolvem os flags conforme D0.W, como no Mac.
- `68000-mac.patch`: as convenções de chamada dos traps (registradores para
  os traps do sistema, pilha no estilo Pascal para os da Toolbox).

E gera `traps.json` e `types.json` em `~/.cache/hcx-ghidra` a partir das
Multiversal Interfaces (`mktraps.py`, `mktypes.py`).

## Usar

    python3.12 -m tools.xemu.compare ARQUIVO PILHA --dump NOME codigo.bin
    HCX_GHIDRA=PASTA_DO_GHIDRA sh tools/ghidra/decompile.sh codigo.bin saida.c

Saem `saida.c` (todas as funções em C), `saida.asm` (a listagem, com o
destino de cada chamada e o nome da rotina nos traps que despacham por
seletor, como `PBHOpenDeny` ou `PBGetCatInfo`) e `saida.log`.

Os scripts do Ghidra:

- `MacTraps.java` (antes da análise): cria só os traps que o código usa, com
  nome, parâmetros e retorno.
- `DecompileAll.java` (depois): acha as funções que a análise perde (prólogos
  `LINK`/`MOVEM`), põe os nomes MacsBug, entende o retorno Pascal
  `movea.l (sp)+,An; jmp (An)`, chama a entrada de `main(XCmdPtr)`, dá nome às
  rotinas de callback do HyperCard (`HC_EvalExpr` e as outras) e escreve o
  `.c` e o `.asm`.
- `MacTypes.java`: monta os tipos do Mac (`HParamBlockRec`, `XCmdBlock`...)
  no Ghidra.

## Como entra no trabalho

O Ghidra mostra o que o código faz; o xemu mostra o que ele devolve. O
caminho é: descompilar, achar no C os casos que mudam o resultado, rodá-los
no xemu contra a versão nativa e, quando o emulador e o C discordam,
confirmar com o watchpoint do xemu (`Machine.watch`) quem escreveu cada
endereço.

Exemplo: o FileCopy 3.8 trava numa leitura de 0 bytes no xemu. Comparando
com o 3.7, a 3.8 calcula o tamanho do bloco de cópia num registrador e não o
guarda na variável local que a sub-rotina de cópia lê (um erro do otimizador
com variáveis de procedimentos aninhados): a leitura usa o que sobrou na
pilha. O emulador estava certo; a versão nativa copia normalmente.

## Créditos e licenças

Ghidra é da NSA, licença Apache 2.0; os patches mudam arquivos dele e seguem
a mesma licença. As Multiversal Interfaces são de Wolfgang Thaller, licença
MIT; os `.json` gerados delas não ficam no repositório.
