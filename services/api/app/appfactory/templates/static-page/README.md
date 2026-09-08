# {{PAGE_TITLE}}

Sahibin bilgisayarında App Factory tarafından oluşturulmuş, basit bir statik sayfa
(docs/M23_APP_FACTORY_SPEC.md).

## Çalıştırma

```
python -m http.server {{PORT}} --bind 127.0.0.1
```

Sonra tarayıcıda `http://127.0.0.1:{{PORT}}/` adresini açın.

## Testler

```
node tests/run.js
```
