from flask import Flask
from uvwsgi import run

app = Flask(__name__)


@app.route('/')
def index():
    return 'hello world!'

if __name__ == '__main__':
    run(app, ('0.0.0.0', 8088))
