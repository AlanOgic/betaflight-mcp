# Betaflight MCP Server — Synthèse & Architecture

## Contexte
MCP (Model Context Protocol) n'est pas limité au SaaS.
Il peut s'interfacer avec n'importe quel processus local,
port série, application desktop, base de données locale, etc.

## Cas d'usage
Contrôler / configurer un Flight Controller (FC) Betaflight
depuis un Skill (Alexa, LLM custom, etc.) via un MCP Server Python.

---

## Architecture générale

```
┌─────────────────┐     MCP Protocol     ┌──────────────────────────┐
│   Ton Skill     │ ◄──────────────────► │   MCP Server (Python)    │
│ (Alexa/LLM/...) │                      │   betaflight_mcp/        │
└─────────────────┘                      └──────────┬───────────────┘
                                                     │ MSP Protocol
                                                     │ (pyserial / USB UART)
                                          ┌──────────▼───────────────┐
                                          │   Flight Controller       │
                                          │   (Betaflight Firmware)   │
                                          └──────────────────────────┘
```

---

## Stack technique

| Couche        | Technologie         | Rôle                                  |
|---------------|---------------------|---------------------------------------|
| Skill         | Python / Node.js    | Interface utilisateur / LLM           |
| MCP Server    | Python 3.13         | Expose les tools MCP                  |
| Protocole FC  | MSP (MultiWii)      | Communication série avec Betaflight   |
| Liaison série | pyserial            | Accès port USB/UART depuis Python     |

---

## Protocole MSP (MultiWii Serial Protocol)

### Structure d'une trame MSP
```
$M<  [size] [cmd] [payload...] [checksum]
$M>  [size] [cmd] [payload...] [checksum]   ← réponse FC
```

- `$M<` : commande envoyée AU FC
- `$M>` : réponse reçue DU FC
- Checksum : XOR de [size, cmd, payload...]

### Commandes MSP principales

| Commande          | Code  | Direction | Description              |
|-------------------|-------|-----------|--------------------------|
| MSP_STATUS        | 101   | Request   | État général du FC       |
| MSP_RAW_IMU       | 102   | Request   | Données IMU (gyro/accel) |
| MSP_ANALOG        | 110   | Request   | Tension batterie         |
| MSP_PID           | 112   | Request   | Lire valeurs PID         |
| MSP_SET_PID       | 202   | Set       | Écrire valeurs PID       |
| MSP_RC_TUNING     | 111   | Request   | Lire rates               |
| MSP_SET_RC_TUNING | 204   | Set       | Écrire rates             |
| MSP_MODE_RANGES   | 34    | Request   | Modes activés            |
| MSP_EEPROM_WRITE  | 250   | Set       | Sauvegarder config       |
| MSP_REBOOT        | 68    | Set       | Redémarrer le FC         |

---

## Tools MCP exposés

```
get_fc_status       → MSP_STATUS        (arming flags, cycle time...)
get_imu_data        → MSP_RAW_IMU       (gyro x/y/z, accel x/y/z)
get_battery         → MSP_ANALOG        (voltage, current, mAh)
get_pid_values      → MSP_PID           (P/I/D par axe)
set_pid_values      → MSP_SET_PID       (écriture P/I/D)
get_rates           → MSP_RC_TUNING     (rates, expo, throttle)
set_rates           → MSP_SET_RC_TUNING (écriture rates)
get_modes           → MSP_MODE_RANGES   (modes RC)
save_config         → MSP_EEPROM_WRITE  (persistance EEPROM)
reboot_fc           → MSP_REBOOT        (redémarrage FC)
list_serial_ports   → (local)           (liste ports dispo)
connect             → (local)           (ouvrir connexion série)
disconnect          → (local)           (fermer connexion)
```

---

## Structure du projet

```
betaflight_mcp/
├── NOTE.md                    ← ce fichier
├── requirements.txt           ← dépendances Python
├── main.py                    ← point d'entrée MCP Server
├── mcp/
│   ├── __init__.py
│   ├── server.py              ← MCP Server loop (stdio/SSE)
│   └── tools.py               ← définition des tools MCP
├── betaflight/
│   ├── __init__.py
│   ├── msp.py                 ← MSP Protocol (encode/decode)
│   ├── serial_conn.py         ← gestion port série (pyserial)
│   └── commands.py            ← commandes haut niveau
└── config/
    └── settings.py            ← configuration (port, baudrate...)
```

---

## Connexion série avec pyserial

```python
import serial
ser = serial.Serial(
    port='/dev/ttyUSB0',   # ou 'COM3' sur Windows
    baudrate=115200,
    timeout=1
)
```

**Baudrate Betaflight** : 115200 par défaut (configurable dans Betaflight Configurator)

---

## Points d'attention

- **WebUSB** (Betaflight Configurator web) est limité au navigateur → utiliser pyserial directement
- Toujours **sauvegarder** (MSP_EEPROM_WRITE) après un SET
- Ne jamais armer le FC via MCP sans sécurité physique
- Vérifier le port série : `ls /dev/ttyUSB*` (Linux) ou Gestionnaire de périphériques (Windows)

---

## Prochaines étapes

- [ ] Implémenter le parsing complet MSP_PID response
- [ ] Ajouter authentification sur le MCP Server
- [ ] Gérer la reconnexion automatique
- [ ] Ajouter des tools pour Blackbox / OSD
- [ ] Tests unitaires avec mock serial
