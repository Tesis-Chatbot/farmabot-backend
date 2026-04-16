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
        
        # El Front manda store_id = 1
        store_id_front = int(payload.get("store_id", 1)) 
        
        # MAPEADOR: Traducimos el ID 1 al código 101 que usa medicaments_stock
        # Si tienes más sucursales, podrías usar un diccionario o buscarlo en una tabla 'stores'
        mapeo_sucursales = {1: 101, 2: 102} 
        store_para_stock = mapeo_sucursales.get(store_id_front, 101)

        if not items:
            raise HTTPException(status_code=400, detail="Carrito vacío")

        # --- 1. VALIDACIÓN PREVIA DE STOCK ---
        for item in items:
            barcode = int(item["barcode"])
            qty_solicitada = int(item["quantity"])
            
            # Buscamos en 'medicaments_stock' usando 'store=101'
            stock_res = supabase.table("medicaments_stock") \
                .select("stock, medicaments(name)") \
                .eq("barcode", barcode) \
                .eq("store", store_para_stock).execute()

            if not stock_res.data:
                raise HTTPException(
                    status_code=404, 
                    detail=f"Producto {barcode} no registrado en sucursal {store_para_stock}"
                )
            
            stock_actual = stock_res.data[0]["stock"]
            nombre_prod = stock_res.data[0].get("medicaments", {}).get("name", "Producto")

            if stock_actual < qty_solicitada:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Stock insuficiente para {nombre_prod}. Disponible: {stock_actual}"
                )

        # --- 2. INSERTAR TICKET (CABECERA) ---
        card_number = payload.get("card_number")
        card_id = None
        if card_number:
            c_res = supabase.table("loyalty_cards").select("id").eq("card", card_number).execute()
            if c_res.data: card_id = c_res.data[0]["id"]

        ticket_data = {
            "total": float(payload["total"]),
            "card_id": card_id,
            "card": int(card_number) if card_number else None,
            "payment_method": payload.get("payment_method", "Efectivo"),
            "store_id": store_id_front, # Aquí guardamos el '1'
            "folio": 0
        }
        
        ticket_res = supabase.table("tickets").insert(ticket_data).execute()
        nuevo_ticket_id = ticket_res.data[0]["id"]

        # Generar Folio
        fecha_str = datetime.datetime.now().strftime("%Y%m%d")
        folio_numerico = int(f"{fecha_str}{store_para_stock}{nuevo_ticket_id}")
        supabase.table("tickets").update({"folio": folio_numerico}).eq("id", nuevo_ticket_id).execute()

        # --- 3. DETALLES Y ACTUALIZACIÓN REAL DE STOCK ---
        for item in items:
            b_code = int(item["barcode"])
            qty = int(item["quantity"])

            # A. Guardar detalle del ticket
            supabase.table("ticket_details").insert({
                "ticket_id": nuevo_ticket_id,
                "barcode": b_code,
                "quantity": qty,
                "price_at_sale": float(item["price"])
            }).execute()

            # B. Restar Stock usando el ID 101
            # Primero obtenemos el valor más fresco
            curr = supabase.table("medicaments_stock") \
                .select("stock") \
                .eq("barcode", b_code) \
                .eq("store", store_para_stock).execute()
            
            nuevo_stock = curr.data[0]["stock"] - qty

            # Actualizamos la tabla medicaments_stock
            supabase.table("medicaments_stock") \
                .update({"stock": nuevo_stock}) \
                .eq("barcode", b_code) \
                .eq("store", store_para_stock).execute()

        return {
            "status": "success", 
            "folio": folio_numerico,
            "ticket_id": nuevo_ticket_id
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error: {str(e)}")
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