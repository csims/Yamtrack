web: cd src && python manage.py runserver
worker: cd src && celery -A config worker --loglevel DEBUG
beat: cd src && celery -A config beat --scheduler django --loglevel DEBUG
css: npm run tailwind:watch
