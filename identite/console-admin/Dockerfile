FROM python:3.13.18-slim-trixie

# Utilisateur sans privilèges ; le volume /data (jeton Synapse, journal d'audit) lui appartient.
RUN useradd --uid 1000 --create-home console \
 && mkdir -p /data \
 && chown console:console /data

WORKDIR /app
# Les outils de test sont inclus : les tests se lancent dans le conteneur, même sans accès à Internet.
COPY requirements.txt requirements-tests.txt ./
RUN pip install --no-cache-dir -r requirements-tests.txt
COPY app.py auth.py enrolement.py revocation.py synapse_token.py pytest.ini ./
COPY static ./static
COPY tests ./tests

USER console
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
