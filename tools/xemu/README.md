# xemu: o emulador 68000 usado para portar XCMDs

Ferramenta de desenvolvimento. **Não faz parte do HCX** nem do AppImage: o HCX
só tem externos nativos, em Python (`hcx/xcmds.py`). O xemu roda o código
68k original de um XCMD/XFCN ao lado da versão nativa, com os mesmos
parâmetros, para comparar os dois resultados e para entender o que o original
faz antes de portar (quais traps da Toolbox e quais callbacks do HyperCard ele
usa).

## Uso

    sh tools/xemu/cpu/build.sh          # compila o núcleo (tools/xemu/_hcxcpu.so)
    python3 -m tools.xemu.compare ARQUIVO PILHA --list
    python3 -m tools.xemu.compare ARQUIVO PILHA NOME [PARÂMETRO ...]
    python3 -m tools.xemu.compare ARQUIVO PILHA --batch CASOS.json

`ARQUIVO` é qualquer coisa que o HCX abre (pilha, .sit, imagem de disco).
Rode da raiz do repositório com o Python que tem GTK. Os arquivos que os
externos leem e gravam ficam numa pasta temporária, nunca em
`~/.local/share/hcx`. As globais que o externo muda também são comparadas.
`XEMU_TRACE=1` mostra cada callback e, quando o código 68k falha, as últimas
instruções que rodaram antes.

Exemplo de `CASOS.json` com arquivos de teste:

    {"files": {"HD:Notas:a.txt": "olá", "HD:Vazia:": null},
     "cases": [["GetFilesInDir", ["HD:Notas:", "TEXT", "\n"]],
               ["AppendDataToFile", ["mais", "4", "HD:Notas:a.txt"]]]}

## Partes

- `cpu/`: Musashi 3.4 (Karl Stenerud, licença MIT) com o SoftFloat de John
  Hauser (licença própria, livre), mais `hcxcpu.c`, a ponte com o Python.
- `mac68k.py`: memória de 16 MB, heap com handles, pilha, globais de memória
  baixa e do QuickDraw.
- `toolbox.py`: os traps da Toolbox (memória, recursos, strings, datas,
  QuickDraw de retângulos, tela, International Utilities e Script Manager
  para o script romano) e `filemgr.py`, o File Manager sobre a pasta
  temporária.
- `sane.py`: o SANE, ponto flutuante do Mac (Pack4 e as conversões decimais
  do Pack7), com a precisão de um double do Python.
- `xcmdrun.py`: o XCmdBlock e os callbacks do HyperCard. Os números dos
  callbacks do 2.x (29 em diante) foram lidos nas rotinas de cola dos próprios
  externos do acervo de teste; os marcados com `?` aparecem em um só externo.
- `compare.py`: o comparador.

Nada aqui desenha: traps de janela e de desenho não fazem nada, e o que
falta para um externo rodar aparece como "not emulated: trap XXXX".
