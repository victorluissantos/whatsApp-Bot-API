from decouple import Config, RepositoryEnv
from mysql.connector import errorcode

import mysql.connector

class Database:

    # def __init__(self, table, name):
    #     self.table = table
    #     self.name = name

    def connect(self, config=None):
        try:
            connection = mysql.connector.connect(host=config['host'], database=config['database'], user=config['user'], password=config['password'])
            connection.set_charset_collation('utf8mb4', 'utf8mb4_bin')
            return connection  # Retorna apenas a conexão
        except mysql.connector.Error as err:
            error_message = None
            if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                error_message = "Something is wrong with your user name or password"
            elif err.errno == errorcode.ER_BAD_DB_ERROR:
                error_message = "[#21]Database does not exist"
            else:
                error_message = str(err)
            return None  # Retorna None para a conexão em caso de erro


    def getWebhook(self, connection, instancie_name):
        try:
            query = """
            SELECT 
                w.endpoint,
                w.method,
                i.*
            FROM
                notify.webhooks w
                INNER JOIN
                notify.instancies i ON w.instancie_id = i.id
            WHERE
                i.name = %s;
            """
            cursor = connection.cursor()
            cursor.execute(query, (instancie_name,))
            record = cursor.fetchone()
            return record
        except mysql.connector.Error as e:
            print("Error reading webhook data from MySQL table:", e)
            return None
        finally:
            if connection.is_connected():
                cursor.close()
                print("[#76]MySQL cursor is closed")

if __name__ == "__main__":
    
    db = Database()

    config = {
        'host':'',
        'database':'',
        'user':'',
        'password':''
        }
    connection = db.connect(config)

    # env = Config(RepositoryEnv('.env'))
    # webhook = db.getWebhook(env.get('FLASK_NAME'))
    weebhook = db.getWebhook(connection, "INS-FLK3")
    print(str(weebhook))