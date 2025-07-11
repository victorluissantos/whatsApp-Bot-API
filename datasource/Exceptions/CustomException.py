class NavegadorNaoConectadoException(Exception):
    def __init__(self, message="Verifique se a instância está ativa e conectada no endpoint 'status'"):
        self.message = message
        super().__init__(self.message)
