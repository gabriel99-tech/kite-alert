# 🪁 Kite Alert

Avisa por Telegram cuando la previsión da una sesión de kite en alguno de
tus spots, y te dice qué cometa llevar.

Spots configurados:

- **Oliva (Rabdells)**, a la altura de Oliva Nova
- **Illa de Arousa**
- **Pantano de San Juan**
- **Valmayor**
- **El Atazar**

## Qué hace

- Consulta la previsión horaria de los próximos 3 días en
  [Open-Meteo](https://open-meteo.com) (gratis, sin clave), con el modelo
  AROME de Météo-France.
- Busca sesiones: al menos 2 horas seguidas, de día y entre las 09:00 y las
  20:00, con viento dentro del rango de tus cometas, dirección válida para el
  spot, sin lluvia y sin rachas excesivas.
- De cada sesión da el **viento constante** (media, mínimo y máximo), la
  **racha máxima**, la dirección y la **cometa recomendada**.
- Solo manda Telegram cuando hay novedades: aparece una sesión, cambia la
  cometa recomendada o una sesión prevista deja de salir.

Ejemplo:

```text
🪁 KITE ALERT
🆕 Sesión nueva: Oliva (Rabdells), mié 14

Oliva (Rabdells)
🟢 mié 14 · 12:00–15:00
   💨 Constante 20 kt (18–22) · Rachas 27 kt · 🧭 E
   🪁 Peter Lynn 10 m — en su rango ideal

🔴 Valmayor: sin sesión en 3 días.
```

🟢 = la cometa va en su rango ideal y aguanta las rachas. 🟡 = navegable pero
justo o pasado de potencia.

## Instalación

Necesitas Python 3.10 o superior.

```bash
pip install -r requirements.txt
```

## Uso

```bash
python kite_alert.py --once           # consulta una vez
python kite_alert.py --once --force   # envía el parte aunque no haya novedades
python kite_alert.py                  # se queda consultando cada 3 horas
```

El parte siempre se muestra por pantalla. Lo último avisado se guarda en
`state.json`; bórralo si quieres empezar de cero.

## Telegram

1. En Telegram, habla con `@BotFather`, usa `/newbot` y copia el token.
2. Manda cualquier mensaje a tu bot.
3. Abre `https://api.telegram.org/bot<TOKEN>/getUpdates` y busca el `chat.id`.
4. Copia `.env.example` a `.env` y rellena:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Cómo elige la cometa

En `kite_alert.py`, cada cometa tiene un rango útil y un rango ideal de
viento constante:

| Cometa | Útil | Ideal |
| --- | --- | --- |
| Duotone Rebel 12 m | 13–25 kt | 15–22 kt |
| Peter Lynn 10 m | 15–28 kt | 17–25 kt |

Para cada sesión se elige, por este orden, la cometa que:

1. aguanta la racha máxima (la racha no supera su tope útil),
2. tiene el viento constante dentro de su rango ideal,
3. lo tiene más centrado en ese rango.

Por eso con 20 kt constantes y rachas de 27 kt recomienda la 10 m, aunque la
12 m también esté en rango.

Otros límites ajustables:

```python
MAX_GUST_KT = 31.0        # racha máxima admitida
MAX_GUST_FACTOR = 1.45    # racha / viento constante: por encima, muy racheado
RAIN_LIMIT_MM_H = 0.5
START_HOUR = 9
END_HOUR = 20
MIN_SESSION_HOURS = 2
```

## Spots y direcciones

```python
SPOTS = {
    "Oliva (Rabdells)": {
        "lat": 38.897, "lon": -0.052,
        "dir_from": 0, "dir_to": 135,
    },
    ...
}
```

`dir_from` y `dir_to` delimitan, en grados y en sentido horario, las
direcciones desde las que el viento vale para ese spot. Con `None` vale
cualquiera. Ahora mismo solo Oliva tiene sector (N a SE, descarta el viento
de tierra); los otros cuatro aceptan cualquier dirección hasta que los ajustes.

Las coordenadas son aproximadas: muévelas al punto exacto donde navegas.

## Dejarlo funcionando solo

**En Windows:** el Programador de tareas puede lanzar `run_kite_alert.bat`
cada 3 horas:

```text
schtasks /create /tn "Kite Alert" /sc hourly /mo 3 /tr "C:\ruta\a\run_kite_alert.bat"
```

Solo avisa mientras el PC esté encendido.

**Sin PC, con GitHub Actions:** sube esta carpeta a un repositorio de GitHub
y añade `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en *Settings → Secrets and
variables → Actions*. El workflow de `.github/workflows/kite-alert.yml` hace
la consulta cada 3 horas, guarda `state.json` en el propio repositorio y
publica el parte como página web con GitHub Pages:

https://gabriel99-tech.github.io/kite-alert/

La página muestra las sesiones de cada spot y, haya sesión o no, la hora de
más viento prevista cada día. En local se genera en `site/index.html`.

## Aviso

Una previsión no sustituye la comprobación del viento real. En embalses el
relieve produce rachas y cambios locales que el modelo no representa bien.
