"""Versão do contrato entre serviços.

Incrementar sempre que um payload ou evento mudar de forma incompatível.
O `ai-worker` recusa job com versão maior que a dele e devolve para a fila,
em vez de processar com um formato que não entende — isso evita que um
deploy parcial corrompa dados.
"""

CONTRACT_VERSION = 1
