import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from database import supabase
import datetime
import re

load_dotenv()

# 1. Definir los metadatos de las etiquetas
tags_metadata = [
    {
        "name": "Catalogo de Productos",
        "description": "Endpoints para consultar medicinas, stock y promociones vigentes.",
    },
    {
        "name": "Punto de Venta",
        "description": "Operaciones relacionadas con el procesamiento de tickets y actualización de inventario.",
    },
    {
        "name": "Tarjetas de Lealtad",
        "description": "Consulta de historial de clientes y beneficios por puntos.",
    },
]

# 2. Crear la instancia de FastAPI
app = FastAPI(
    title="Chatbot Farmacia",
    openapi_tags=tags_metadata
)

# 3. Configuración de CORS
origins_str = os.getenv("CORS_ORIGINS", "")
origins = origins_str.split(",") if origins_str else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Endpoints ---

@app.get("/", tags=["Sistema"])
def read_root():
    return {"message": "Conexión exitosa, bienvenido a FarmaBot"}

## CATALOGO DE PRODUCTOS
@app.get("/medicamentos", tags=["Catalogo de Productos"])
def get_medicamentos():
    # Consultamos medicamentos con su stock y sus promociones activas
    # Traemos: datos de medicina, stock, y de la tabla promotion traemos amount y la descripción del tipo
    query = """
        *,
        medicaments_stock(stock),
        promotion(
            id,
            amount,
            active,
            promotion_types(description)
        )
    """
    response = supabase.table("medicaments").select(query).execute()
    
    productos_finales = []
    data = response.data if response.data else []
    
    for item in data:
        # 1. Cálculo de Stock (como ya lo tenías)
        stock_entries = item.get('medicaments_stock') or []
        total_stock = sum(s.get('stock', 0) for s in stock_entries)
        
        # 2. Filtrado de Promociones Activas
        # Supabase trae todas, pero solo queremos enviar las que 'active' sea True
        all_promotions = item.get('promotion') or []
        active_promos = []
        
        for p in all_promotions:
            if p.get('active'):
                # Simplificamos el objeto de promoción para el Front
                tipo_desc = p.get('promotion_types', {}).get('description', 'General')
                active_promos.append({
                    "id": p.get('id'),
                    "tipo": tipo_desc,
                    "valor": p.get('amount')
                })

        # 3. Limpieza del objeto para el Front
        nuevo_item = item.copy()
        nuevo_item['stock'] = total_stock
        nuevo_item['promociones'] = active_promos # Agregamos la lista limpia
        
        # Borramos las relaciones crudas de Supabase para no ensuciar el JSON
        keys_to_del = ['medicaments_stock', 'promotion']
        for key in keys_to_del:
            if key in nuevo_item:
                del nuevo_item[key]
            
        productos_finales.append(nuevo_item)
        
    return productos_finales



## PUNTO DE VENTA
@app.post("/ventas", tags=["Punto de Venta"])
async def procesar_venta(payload: dict = Body(...)):
    try:
        items = payload.get("items", [])
        # Obtenemos la sucursal del payload (por defecto 1 si no viene)
        store_id = int(payload.get("store_id", 1)) 
        
        if not items:
            raise HTTPException(status_code=400, detail="Carrito vacío")

        # 1. Buscar tarjeta de lealtad
        card_id = None
        card_number = payload.get("card_number")
        if card_number:
            card_res = supabase.table("loyalty_cards").select("id").eq("card", card_number).execute()
            if card_res.data:
                card_id = card_res.data[0]["id"]

        # 2. Insertar Ticket (Cabecera inicial)
        ticket_data = {
            "total": float(payload["total"]),
            "card_id": card_id,
            "card": int(card_number) if card_number else None,
            "payment_method": payload.get("payment_method", "Efectivo"),
            "store_id": store_id,
            "folio": 0
        }
        
        ticket_res = supabase.table("tickets").insert(ticket_data).execute()
        if not ticket_res.data:
            raise Exception("Error al crear la cabecera del ticket")
            
        nuevo_ticket_id = ticket_res.data[0]["id"]

        # 3. Generar Folio Numérico (BigInt)
        # Formato: AAAAMMDD + ID_TIENDA (2 dígitos) + ID_TICKET (4 o más dígitos)
        fecha_str = datetime.datetime.now().strftime("%Y%m%d")
        # Generamos un número único que quepa en un BigInt
        folio_numerico = int(f"{fecha_str}{store_id:02d}{nuevo_ticket_id}")
        
        # Actualizamos el ticket con su folio real
        supabase.table("tickets").update({"folio": folio_numerico}).eq("id", nuevo_ticket_id).execute()

        # 4. Detalles y Actualización de Stock
        for item in items:
            # A. Detalle del ticket
            detalle = {
                "ticket_id": nuevo_ticket_id,
                "barcode": int(item["barcode"]),
                "quantity": int(item["quantity"]),
                "price_at_sale": float(item["price"]),
                "promotion_id": item.get("promotion_id") if item.get("promotion_id") else None
            }
            supabase.table("ticket_details").insert(detalle).execute()

            # B. Actualizar Stock en la sucursal específica
            stock_res = supabase.table("medicaments_stock") \
                .select("stock") \
                .eq("barcode", item["barcode"]) \
                .eq("store", store_id).execute()

            if stock_res.data:
                actual_stock = stock_res.data[0]["stock"]
                nuevo_stock = actual_stock - int(item["quantity"])
                
                supabase.table("medicaments_stock") \
                    .update({"stock": nuevo_stock}) \
                    .eq("barcode", item["barcode"]) \
                    .eq("store", store_id).execute()

        return {
            "status": "success", 
            "ticket_id": nuevo_ticket_id, 
            "folio": folio_numerico,
            "store_id": store_id
        }

    except Exception as e:
        print(f"Error detallado en Venta: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
## TARJETAS DE LEALTAD
@app.get("/clientes/{num_tarjeta}", tags=["Tarjetas de Lealtad"])
async def get_cliente_by_card(num_tarjeta: int):
    try:
        # Enriquecemos la query con los campos de la cabecera del ticket
        query = """
            *,
            tickets!tickets_card_id_fkey (
                id,
                folio,
                created_at,
                total,
                payment_method,
                store_id,
                ticket_details (
                    barcode,
                    quantity,
                    price_at_sale,
                    promotion_id,
                    medicaments (
                        name,
                        promotion (
                            id,
                            barcode,
                            promotion_type,
                            amount,
                            active
                        )
                    )
                )
            )
        """
        
        response = supabase.table("loyalty_cards").select(query).eq("card", num_tarjeta).execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

        cliente = response.data[0]
        tickets = cliente.get("tickets", [])
        resumen_promociones = {}

        for t in tickets:
            # Aseguramos que el total sea float/int para evitar errores en el Front
            t["total"] = float(t.get("total", 0))
            
            for detalle in t.get("ticket_details", []):
                # Aseguramos que los precios y cantidades sean numéricos
                detalle["price_at_sale"] = float(detalle.get("price_at_sale", 0))
                detalle["quantity"] = int(detalle.get("quantity", 0))
                
                medicamento = detalle.get("medicaments")
                if not medicamento: continue
                
                promos = medicamento.get("promotion", [])
                promo_activa = next((p for p in promos if p.get("promotion_type") == 2 and p.get("active")), None)

                if promo_activa:
                    barcode = detalle["barcode"]
                    raw_amount = str(promo_activa["amount"])
                    
                    match = re.search(r'(\d+)', raw_amount)
                    if match:
                        meta = int(match.group(1))
                    else:
                        continue

                    if barcode not in resumen_promociones:
                        resumen_promociones[barcode] = {
                            "nombre": medicamento["name"],
                            "acumulado_total": 0,
                            "meta_para_regalo": meta,
                            "texto_promo": raw_amount,
                            "regalos_ganados": 0,
                            "unidades_faltantes": 0
                        }
                    
                    resumen_promociones[barcode]["acumulado_total"] += detalle["quantity"]

        # Calcular saldos finales de lealtad
        for barcode, info in resumen_promociones.items():
            total = info["acumulado_total"]
            meta = info["meta_para_regalo"]
            info["regalos_ganados"] = total // meta
            resto = total % meta
            
            if resto == 0 and total > 0:
                info["unidades_faltantes"] = meta
            else:
                info["unidades_faltantes"] = meta - resto

        cliente["resumen_lealtad"] = list(resumen_promociones.values())
        
        return cliente

    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al procesar cliente: {str(e)}")