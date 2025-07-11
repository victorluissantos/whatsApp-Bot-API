from pymongo import MongoClient
from datetime import datetime, timedelta
from decouple import Config, RepositoryEnv
import pymongo
import time

class MongoDBConnector:
    def __init__(self, env):
        self.env = env
        
        # Pegando variáveis do .env
        mongo_user = env("MONGO_USER")
        mongo_password = env("MONGO_PASSWORD")
        mongo_host = env("MONGO_NAME")  # Nome do container no Docker
        mongo_port = env("MONGO_PORT")
        mongo_db = env("MONGO_DB")
        mongo_collection = env("MONGO_COLLECTION")

        # String de conexão com autenticação - usando authSource=admin
        mongo_uri = f"mongodb://{mongo_user}:{mongo_password}@{mongo_host}:{mongo_port}/{mongo_db}?authSource=admin"

        try:
            # Conectar ao MongoDB
            self.mongo_client = pymongo.MongoClient(mongo_uri)
            self.db = self.mongo_client[mongo_db]
            self.collection = self.db[mongo_collection]

            print("✅ Conectado ao MongoDB com sucesso!")
        except pymongo.errors.ConnectionFailure as e:
            print(f"❌ Erro de conexão com o MongoDB: {e}")
        except pymongo.errors.OperationFailure as e:
            print(f"❌ Erro de autenticação no MongoDB: {e}")


    def buscar_ultimas_mensagens(self):
        # Calcula a data e hora de 8 horas atrás
        oito_horas_atras = datetime.now() - timedelta(hours=4)

        # Pipeline para filtrar e agrupar as mensagens
        pipeline = [
            # Ordena pela data_hora de forma crescente (para pegar a última mais recente depois)
            {"$sort": {"data_hora": 1}},  # Ordena pela data_hora de forma decrescente para pegar as mais recentes
            # Agrupa as mensagens por telefone, mantendo a última mensagem de cada telefone
            {"$group": {
                "_id": "$telefone",
                "ultima_mensagem": {"$last": "$$ROOT"}  # Pega a última mensagem para cada telefone
            }},
            # Filtra apenas as mensagens que atendem ao critério das últimas 8 horas e terminam com "?"
            {"$match": {
                "ultima_mensagem.data_hora": {"$gt": oito_horas_atras},  # Mensagens de até 8 horas atrás
                "ultima_mensagem.mensagem": {"$regex": "\\?$", "$options": "i"}  # Mensagens que terminam com "?"
            }}
        ]

        mensagens = []
        telefones = []

        # Executa o pipeline
        for doc in self.collection.aggregate(pipeline):
            mensagens.append(doc["ultima_mensagem"]["mensagem"])
            telefones.append(doc["ultima_mensagem"]["telefone"])

        return mensagens, telefones

# Exemplo de uso:
if __name__ == "__main__":
    mgd = MongoDBConnector(".env")
    mensagens, telefones = mgd.buscar_ultimas_mensagens()
    for msg, tel in zip(mensagens, telefones):
        print(f"Telefone: {tel}, Mensagem: {msg}")