# z1-agent

Agente de automatización para **facturación e inventario** de una cervecería artesanal
(Madre Monte), integrado con **Alegra** (contabilidad Colombia) y **Google Sheets**.

## Proceso oficial: "generar factura"

Script principal: [`src/facturar_remisiones_auto.py`](src/facturar_remisiones_auto.py)

Factura en Alegra las remisiones pendientes de un Google Sheet y descuenta inventario.

### Flujo
```
Google Sheet (Remisiones) → filas pendientes → crear factura Alegra (draft)
   → descontar inventario → marcar "Facturado" en el Sheet → registrar (anti-duplicados)
```

### Qué factura
Filas con `Facturar = Sí` (col 22) y `Facturado` vacío (col 26 / columna AA).
La hoja se descarga sola desde su export público; no se versiona en el repo.

### Uso
```bash
python -m pip install -r requirements.txt
export MADREMONTE_KEY='<clave maestra>'                       # descifra credenciales (.env.enc)

python src/facturar_remisiones_auto.py            # DRY-RUN: previsualiza (no crea nada)
python src/facturar_remisiones_auto.py --crear    # crea facturas en Alegra (draft) + inventario
python src/facturar_remisiones_auto.py --auth-sheets   # autoriza (1 vez) escritura al Sheet
```
Siempre correr el **dry-run** primero, revisar el preview y confirmar antes de `--crear`.

### Efectos de `--crear`
1. Crea la factura en Alegra como `draft` (cliente resuelto por nombre vía API; respaldo CSV local).
2. Descuenta inventario (PTL = granel/litros en barril, PTB = botellas 330 ml; DOM01 no afecta stock).
3. Marca `Sí` en la columna **AA (Facturado)** de la fila del Sheet (OAuth, no bloqueante).
4. Registra la factura en un **ledger anti-duplicados** local.

### Mapeo de productos (código → id Alegra / precio COP)
| Código | Producto | id | Precio |
|---|---|---|---|
| PTB01 | Golden Ale 330ml | 64 | 8.500 |
| PTB02 | Irish Red 330ml | 65 | 8.500 |
| PTB03 | APA 330ml | 66 | 8.500 |
| PTB04 | IPA 330ml | 67 | 9.500 |
| PTB05 | Stout 330ml | 68 | 8.500 |
| PTL01 | Golden Ale x litro | 69 | 18.000 |
| PTL02 | Irish Red x litro | 70 | 18.000 |
| PTL03 | APA x litro | 71 | 18.000 |
| PTL04 | IPA x litro | 72 | 19.000 |
| PTL05 | Stout x litro | 73 | 18.000 |
| DOM01 | Domicilio | 58 | variable |

## Datos sensibles (NO incluidos en el repo)
Por privacidad, **no se versionan** datos reales. Solo se enuncian su estructura/ubicación:

- `data/clientes.csv` — base de clientes (cédulas, emails). Ver plantilla vacía:
  [`data/clientes.ejemplo.csv`](data/clientes.ejemplo.csv).
- `inventario_MM.csv` — movimientos de inventario.
- `.env` / `.env.enc` — credenciales (Alegra, DeepSeek, Telegram). Ver `.env.example`.
- Tokens OAuth (`*.pickle`, `credentials*.json`).

Todos están excluidos vía `.gitignore`.

## Seguridad
- La clave maestra `MADREMONTE_KEY` nunca se guarda en archivos.
- Si se expone, rotarla y volver a cifrar las credenciales.
