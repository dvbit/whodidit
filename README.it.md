# Whodidit 🕵️

**Integrazione custom per Home Assistant – sappi esattamente cosa ha attivato i tuoi dispositivi smart.**

Whodidit crea un sensore diagnostico per ogni entità che scegli di monitorare. Ad ogni cambio di stato — o di un attributo rilevante (luminosità, colore, volume...) — il sensore registra **cosa** ha causato il cambiamento (automazione, script, scena, dashboard/UI, account di servizio, o il dispositivo stesso), **chi** l'ha fatto, **quando**, e **quanto è affidabile** la risposta.

> **Attribuzione / ispirazione.** Whodidit è una reimplementazione indipendente, scritta da zero, ispirata al progetto (non più disponibile) [sfox38/whodunnit](https://github.com/sfox38/whodunnit) (licenza MIT). Nessun codice sorgente originale è stato copiato: questa integrazione è stata costruita esclusivamente a partire dal comportamento pubblicamente documentato (README e release notes) di quel progetto, combinato con le API ufficiali di Home Assistant. Tutto il merito del concept va all'autore originale.

---

## Indice

- [Cosa fa](#cosa-fa)
- [Come funziona](#come-funziona)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Domini supportati](#domini-supportati)
- [Stati del sensore](#stati-del-sensore)
- [Attributi del sensore](#attributi-del-sensore)
- [Livelli di confidenza](#livelli-di-confidenza)
- [Evento `whodidit_trigger_detected`](#evento-whodidit_trigger_detected)
- [Esempi di automazione](#esempi-di-automazione)
- [Limiti noti](#limiti-noti)
- [Specifica originale](#specifica-originale)
- [Storico versioni](#storico-versioni)

## Cosa fa

Whodidit crea un **sensore diagnostico** per ogni entità supportata. Ogni cambio di stato o attributo rilevante aggiorna il sensore con:

- **Cosa** ha causato il cambiamento
- **Chi** l'ha fatto (nome della persona, se via UI)
- **Quale** automazione/script/scena specifica è responsabile
- **Quando** è successo (timestamp ISO 8601)
- **Quanto** è affidabile la risposta (`high` / `medium` / `low`)
- Uno **storico** delle ultime 25 attivazioni
- Un attributo di **debug della cache** che spiega come è stata determinata la classificazione

Tutto persiste ai riavvii di Home Assistant (`RestoreEntity`).

## Come funziona

Home Assistant allega un `Context` (API pubblica, stabile e documentata: `id`, `parent_id`, `user_id`) ad ogni cambio di stato. Whodidit ascolta — **una sola volta, globalmente, per tutte le entità monitorate** — gli eventi `automation_triggered`, `script_started` e le chiamate di servizio `scene.turn_on` (le scene non emettono un evento di attivazione dedicato nel core di HA, quindi si usa il contesto della chiamata di servizio), mette in cache questi contesti e li confronta con il contesto di ogni cambio di stato successivo.

**Cascata di rilevamento:**

1. **Hit diretto sul context ID in cache** → il cambiamento è stato causato da un'automazione/script/scena in cache. *Confidenza: Alta.*
2. **Nessun hit, ma `user_id` presente** → una persona (o un account di servizio, se lo `user_id` non ha un'entità `person` collegata e/o è `system_generated`) ha agito via UI/app. *Confidenza: Alta.*
3. **Nessun hit, nessun utente, ma `parent_id` presente** → HA era coinvolto a monte; Whodidit prova a risolvere anche il contesto padre. Risolto → Alta confidenza con la sorgente specifica. Non risolto (catene profonde, integrazioni di terze parti) → classificato come `whodidit.indirect` / **Automazione (Indiretta)**, *Confidenza: Media.*
4. **Nessuna corrispondenza** → il cambiamento proviene direttamente dal dispositivo (pulsante fisico, timer hardware, evento firmware). *Confidenza: Alta.*

I cambi di solo attributo (es. dimmerare una luce senza accenderla/spegnerla) seguono la stessa cascata e sono soggetti a debounce di 2 secondi per entità.

## Installazione

### HACS (consigliata)

1. Apri **HACS** → menu tre puntini → **Repository personalizzati**.
2. Aggiungi `https://github.com/dvbit/whodidit`, categoria **Integration**.
3. Installa **Whodidit**, poi riavvia Home Assistant.

### Manuale

1. Copia `custom_components/whodidit` in `config/custom_components/`.
2. Riavvia Home Assistant.

## Configurazione

**Impostazioni → Dispositivi e Servizi → + Aggiungi integrazione → Whodidit** → scegli l'entità da monitorare. Ripeti per ogni entità (quelle già monitorate sono nascoste dal selettore). Ogni entità monitorata ottiene un proprio sensore e una propria config entry.

## Domini supportati

`switch`, `light`, `fan`, `media_player`, `cover`, `lock`, `vacuum`, `siren`, `humidifier`, `climate`, `remote`, `water_heater`, `valve`, `number`, `select`, `button`, `input_boolean`, `input_button`, `input_number`, `input_select`, `input_text`, `alarm_control_panel`, `timer`.

Le entità helper senza dispositivo fisico ottengono un **dispositivo virtuale** creato automaticamente per ospitare il sensore; viene rimosso automaticamente quando si elimina la voce Whodidit corrispondente.

## Stati del sensore

| Stato | Significato |
|---|---|
| `monitoring` | Attivo, nessun cambiamento ancora registrato |
| `automation` | Un'automazione ha causato il cambiamento |
| `script` | Uno script ha causato il cambiamento |
| `scene` | L'attivazione di una scena ha causato il cambiamento |
| `ui` | Una persona ha agito via dashboard/app |
| `service` | Un account di servizio (Node-RED, AppDaemon...) |
| `device` | Un evento fisico/interno al dispositivo |

## Attributi del sensore

`source_type`, `source_id`, `source_name`, `context_id`, `user_id`, `event_time`, `confidence`, `history_log` (ultime 25 voci), `cache_debug`.

## Livelli di confidenza

| Livello | Significato |
|---|---|
| High | Corrispondenza diretta del contesto, o certezza di nessun coinvolgimento di HA |
| Medium | Coinvolgimento di HA confermato ma sorgente specifica non risolta |
| Low | Possibile misclassificazione da riuso di contesto ESPHome |

## Evento `whodidit_trigger_detected`

Emesso ad **ogni** classificazione, a differenza di un trigger `state` che non si attiverebbe per sorgenti identiche ripetute. Usalo nelle automazioni:

```yaml
automation:
  - alias: "Notifica cambio inatteso luce garage"
    trigger:
      - platform: event
        event_type: whodidit_trigger_detected
        event_data:
          entity_id: light.garage_light
    action:
      - service: notify.mobile_app
        data:
          message: >
            La luce del garage è stata cambiata da
            {{ trigger.event.data.source_name }}
            via {{ trigger.event.data.state }}.
```

## Interazione fisica (v1.1.0) 🖐️

Per ogni entità monitorata puoi abilitare un **sensore binario** che va ON al primo click fisico (classificazione con `source_type = device`) e resta ON finché non scatta una condizione di reset. Il modello è ispirato a [`dvbit/switch_interaction`](https://github.com/dvbit/switch_interaction), con in più un reset "consapevole della stanza".

### Configurazione

Impostabile durante il config flow (step 2) o successivamente via **Configura** sull'entry. Campi:

- **Attiva sensore binario di interazione fisica** — interruttore principale.
- **Tempo di reset (secondi)** — default 300.
- **Finestra conteggio click (secondi)** — default 3 (come `switch_interaction`).
- **Sensore di presenza** — opzionale, dominio `binary_sensor`.
- **Sensore di movimento** — opzionale, dominio `binary_sensor`.

### Logica di reset

Tre modalità, in base ai sensori configurati (**presenza > movimento > solo tempo**):

1. **Con sensore di presenza:** dopo un click fisico, si attende che la presenza sia OFF, poi si contano `reset_lapse_seconds`; se la presenza torna ON durante il conteggio il timer si annulla e riparte quando torna OFF. Il reset manuale via servizio è sempre disponibile.
2. **Con sensore di movimento (senza presenza):** come sopra ma sul sensore di movimento.
3. **Nessun sensore di riferimento:** `reset_lapse_seconds` a partire dal momento in cui il binary è andato ON.

Ogni nuovo click fisico che arriva mentre il conteggio di reset è pendente lo annulla — l'utente sta ancora interagendo.

### Servizio

`whodidit.reset_physical_interaction` — forza OFF e azzera `click_count`. `entity_id` accetta **sia** l'entità monitorata **sia** il sensore binario stesso.

```yaml
service: whodidit.reset_physical_interaction
data:
  entity_id: light.kitchen
```

### Esempio di automazione

```yaml
automation:
  - alias: "Triplo click fisico -> scena cinema"
    trigger:
      - platform: state
        entity_id: binary_sensor.kitchen_light_physical_interaction
        to: "off"
    condition:
      - condition: template
        value_template: "{{ trigger.from_state.attributes.click_count == 3 }}"
    action:
      - service: scene.turn_on
        target:
          entity_id: scene.movie
```

## Esempi di automazione

**Non far spegnere dal sensore di movimento una luce accesa manualmente:**

```yaml
automation:
  - alias: "Spegnimento smart - rispetta il controllo manuale"
    trigger:
      - platform: state
        entity_id: binary_sensor.office_motion
        to: "off"
    condition:
      - condition: not
        conditions:
          - condition: state
            entity_id: sensor.office_light_trigger_source
            state: "device"
    action:
      - service: light.turn_off
        target:
          entity_id: light.office_light
```

**Avviso su classificazione a bassa confidenza:**

```yaml
automation:
  - alias: "Avviso bassa confidenza Whodidit"
    trigger:
      - platform: event
        event_type: whodidit_trigger_detected
        event_data:
          entity_id: light.garage_light
          confidence: low
    action:
      - service: notify.mobile_app
        data:
          message: "Whodidit non è certo della sorgente per la luce del garage."
```

## Card Whodidit (v1.2.0) 🃏

L'integrazione include una **card Lovelace personalizzata** registrata automaticamente — nessuna installazione HACS "Frontend" separata. Dopo l'aggiornamento, fai un hard-refresh del browser (Ctrl/Cmd+Shift+R) o svuota una volta la cache dell'app companion per caricare la nuova risorsa.

Aggiungila a una dashboard:

```yaml
type: custom:whodidit-card
entity: sensor.kitchen_light_trigger_source
```

La card mostra:

- **Ultima interazione** — icona stato, stato localizzato e un piccolo puntino di confidenza colorato (verde = alta, giallo = media, rosso = bassa). Clicca la riga per aprire un **popup storico** con le ultime 25 voci.
- **Interazione fisica** — indicatore discreto Attivo/Inattivo più `click_count` e ora ultimo click (solo se il sensore binario è attivo).
- **Controlli in basso a destra** — un pulsante **reset** (quando il sensore binario esiste) e un **ingranaggio impostazioni** (⚙️) che apre un dialog per modificare al volo: attivazione sensore interazione fisica, tempo di reset, finestra click e sensori di riferimento presenza/movimento. Il salvataggio chiama `whodidit.update_options`, che ricarica l'entry rendendo effettive subito le modifiche.

> **Nota sulla distribuzione.** HACS non mostra nel tab "Frontend" le card che vivono in un repository di tipo *Integration*. È previsto: Whodidit serve la card come asset statico e la registra da sé come risorsa Lovelace, quindi funziona senza aggiungere manualmente la risorsa (in modalità Lovelace *storage*). In modalità *YAML* aggiungi la risorsa a mano: `url: /whodidit/whodidit-card.js`, `type: module`.

## Limiti noti

- **Riavvii di sistema:** i cambiamenti avvenuti mentre HA è offline non vengono rilevati.
- **Riuso contesto ESPHome:** i dispositivi ESPHome possono riusare il contesto HA precedente per ~5s dopo un comando; una pressione fisica in quella finestra può essere classificata come UI con `confidence: low`.
- **Automazioni indirette:** catene profonde o integrazioni di terze parti con propri context chain risolvono come `Automazione (Indiretta)` a confidenza Media.
- **Reti sovraccariche:** la cache dei contesti ha TTL 2 minuti; su sistemi molto congestionati gli eventi possono arrivare fuori ordine.
- **Eventi fisici vs interni:** HA non distingue a livello di contesto una pressione fisica genuina da un evento firmware interno, quindi nemmeno Whodidit può farlo.

## Specifica originale

<details>
<summary>Requisito consolidato usato per costruire questa integrazione</summary>

```
Integrazione custom HA (Python, config_flow), feature-parity completa con
whodunnit v1.3.0, rebrand come "whodidit":

Core detection: sensore diagnostic per entità monitorata, stato =
monitoring/automation/script/scene/ui/service/device. Cascata a 4 livelli:
cache context_id -> user_id (persona/service account) -> parent_id
(risoluzione ricorsiva) -> device. Confidence high/medium/low. Listener
condivisi singoli su eventi automation/script/scene, cache context TTL 2
min con cleanup periodico. Cache identità utente TTL 5 min.

Attributi sensore: source_type, source_id, source_name, context_id,
user_id, event_time, confidence, history_log (ultimi 25, persistente),
cache_debug (matched_entry, age, total_cache_entries).

Evento whodidit_trigger_detected sul bus, payload completo, fired ad ogni
classificazione.

Attribute-only changes: monitoraggio per dominio (light, climate,
media_player, fan, cover, water_heater, humidifier, vacuum), debounce 2s.

ESPHome bleed detection: finestra 5s, confidence low se rilevato riuso
context.

Persistenza & lifecycle: RestoreEntity, virtual device per helper senza
device fisico, availability tracking, diagnostics download,
entity_category diagnostic, SensorDeviceClass.ENUM.

Config flow: picker entità (esclude già tracciate), 1 config entry = 1
sensore = 1 device page.

Domini supportati (21): switch, light, fan, media_player, cover, lock,
vacuum, siren, humidifier, climate, remote, water_heater, valve, number,
select, button, input_boolean, input_button, input_number, input_select,
input_text, alarm_control_panel, timer.

No dashboard card - solo integrazione.

Localizzazione: EN/IT/FR/ES/DE. Output HACS-ready, README EN+IT.
```

</details>

## Storico versioni

### 1.3.2
- Fix: corretto il comportamento di `click_count`. Alla chiusura della finestra di rilevazione il valore ora **persiste** (continua a mostrare l'ultimo treno completato, es. 2); si azzera solo al primo click del treno successivo. Quindi un singolo click mostra 1; dopo la finestra un doppio click mostra 2 (non 3), e nel frattempo resta visibile il valore precedente. Il valore viene ripristinato al riavvio.

### 1.3.1
- Fix: le azioni da UI/dashboard venivano talvolta classificate erroneamente come `device`. Home Assistant emette spesso l'evento `state_changed` risultante con `user_id = None`, mantenendo solo `parent_id` che punta alla service call originaria (comportamento del core, vedi issue #90669). Whodidit ora mette in cache ogni context di service call iniziata da un utente e lo risolve via `parent_id`, così i tap sulla dashboard sono correttamente riportati come `ui` (o `service` per gli account di servizio). Nota: alcune integrazioni emettono un context completamente nuovo senza `user_id` né `parent_id`; quei cambiamenti restano indistinguibili da un evento fisico `device` a livello di context.

### 1.3.0
- Redesign: la **card Whodidit** è ora minimalista e più vicina allo stile Lovelace nativo. La confidenza è mostrata come piccolo puntino colorato (verde/giallo/rosso) invece del badge testuale; cliccando sulla riga stato si apre un **popup storico**; i controlli reset e ingranaggio impostazioni sono in basso a destra della card.

### 1.2.1
- Fix: `click_count` ora conta i click **solo all'interno della finestra di rilevazione** (un "treno di click"). Alla chiusura della finestra il contatore si azzera, quindi il click fisico successivo riparte da 1 — un singolo click mostra 1, un doppio click successivo mostra 2 (non 3). Il contatore è indipendente dal tempo di reset del sensore binario e non viene più ripristinato al riavvio.

### 1.2.0
- Novità: **card Whodidit** inclusa (`custom:whodidit-card`) auto-registrata dall'integrazione — ultima interazione, blocco interazione fisica, timeline storico (25 voci) e ingranaggio impostazioni per modificare le opzioni al volo.
- Novità: servizio `whodidit.update_options` a supporto del dialog impostazioni della card.
- Il manifest ora dichiara le dipendenze `frontend` + `http` per servire l'asset statico.

### 1.1.1
- Novità: icona dell'integrazione (lente d'ingrandimento con un `?` — il motivo classico "chi è stato?") aggiunta come `icon.png` / `icon@2x.png` nella cartella del componente. Home Assistant la mostra automaticamente nella pagina Integrazioni; su GitHub / HACS lo stesso asset appare nella card del repo.

### 1.1.0
- Novità: **sensore binario di interazione fisica** opzionale per entità con attributo `click_count` (modello ispirato a [`dvbit/switch_interaction`](https://github.com/dvbit/switch_interaction)).
- Novità: auto-reset a tre modalità (presenza > movimento > solo tempo) più reset manuale via nuovo servizio `whodidit.reset_physical_interaction`.
- Novità: config flow in due step (selezione entità + opzioni interazione fisica) e Options Flow completo per modificare i parametri in seguito.

### 1.0.1
- Fix: `HTTP 400` all'apertura del config flow — il selettore di entità passava `exclude_entities=None`, che fallisce la validazione voluptuous nel frontend. Ora `exclude_entities` è omesso se nessuna entità è già monitorata.
- Fix: campo `version` in `manifest.json` allineato alla forma completa `MAJOR.MINOR.PATCH` per i loader più stretti di HA.

### 1.0 — rilascio iniziale
- Reimplementazione a feature-parity completa come da specifica sopra.
