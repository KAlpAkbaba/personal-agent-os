# {{APP_TITLE}}

Basit bir görev takip uygulaması. Sahibin bilgisayarında, App Factory tarafından
oluşturuldu (docs/M23_APP_FACTORY_SPEC.md).

## Çalıştırma

```
python -m http.server {{PORT}} --bind 127.0.0.1
```

Sonra tarayıcıda `http://127.0.0.1:{{PORT}}/` adresini açın.

## Kullanım

- Yeni bir görev eklemek için üstteki kutuya yazıp **Ekle**'ye basın.
- Bir görevi tamamlandı olarak işaretlemek için kutucuğuna tıklayın.
- Görevler tarayıcının `localStorage`'ında saklanır; sayfa yenilendiğinde kaybolmaz.

## Testler

```
node tests/run.js
```

`logic.js` içindeki saf fonksiyonları (ekleme, tamamlama, kalıcılık) test eder.
