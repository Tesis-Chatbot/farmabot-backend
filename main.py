import os
import traceback
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Body, APIRouter, Query
from fastapi.middleware.cors import CORSMiddleware
from database import supabase
from datetime import datetime
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
    {
        "name": "Analiticas",
        "description": "Monitoreo de rendimiento del bot, uso por sucursal y estadísticas de usuario.",
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
def get_medicamentos(store_id: int = Query(..., description="ID de la sucursal (ej. 5)")):
    try:
        query = """
            *,
            medicaments_stock(stock, store_id),
            promotion(
                id,
                amount,
                active,
                promotion_types(description)
            )
        """
        
        # Consultamos a Supabase aplicando el filtro de store_id en la relación de stock
        response = supabase.table("medicaments") \
            .select(query) \
            .eq("medicaments_stock.store_id", store_id) \
            .execute()
        
        if not response.data:
            return []

        productos_finales = []
        
        for item in response.data:
            # --- FILTRADO DE STOCK ---
            stock_list = [s for s in (item.get('medicaments_stock') or []) if s.get('store_id') == store_id]

            if stock_list:
                total_stock = sum(s.get('stock', 0) for s in stock_list)
                
                # --- PROCESAMIENTO DE PROMOCIONES ---
                raw_promotions = item.get('promotion') or []
                active_promos = []
                
                for p in raw_promotions:
                    if p.get('active'):
                        tipo_desc = p.get('promotion_types', {}).get('description', 'General')
                        active_promos.append({
                            "id": p.get('id'),
                            "tipo": tipo_desc,
                            "valor": p.get('amount')
                        })

                # --- LIMPIEZA DE OBJETO FINAL ---
                productos_finales.append({
                    "id": item.get("id"),
                    "barcode": item.get("barcode"),
                    "name": item.get("name"),
                    "brand": item.get("brand"),
                    "price": item.get("price"),
                    "lab": item.get("lab"),
                    "stock": total_stock,
                    "promociones": active_promos
                })

        return productos_finales

    except Exception as e:
        print(f"Error detectado: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al sincronizar el inventario con promociones")

@app.post("/promociones", tags=["Catalogo de Productos"])
async def gestionar_promocion(payload: dict = Body(...)):
    try:
        barcode = int(payload.get("barcode"))
        promo_type = int(payload.get("promotion_type"))
        active = bool(payload.get("active", True))
        
        # Procesamiento del valor a guardar
        if promo_type == 1: 
            # Porcentaje: 5 -> "0.05"
            raw_amount = float(payload.get("amount", 0))
            valor_final = str(raw_amount / 100)
            
        elif promo_type == 2:
            # N+M: Guardamos como TEXTO "7+4"
            n = payload.get("n_value", "1")
            m = payload.get("m_value", "1")
            valor_final = f"{n}+{m}"
            
        else:
            # Tipos 3 y 4 (Precios/Montos): Guardamos el número como string
            valor_final = str(payload.get("amount", 0))

        # --- LÓGICA DE UPSERT ---
        check = supabase.table("promotion").select("id").eq("barcode", barcode).execute()
        
        promo_data = {
            "barcode": barcode,
            "promotion_type": promo_type,
            "amount": valor_final, # Ahora es STRING
            "active": active
        }

        if check.data:
            res = supabase.table("promotion").update(promo_data).eq("id", check.data[0]["id"]).execute()
        else:
            res = supabase.table("promotion").insert(promo_data).execute()

        return {"status": "success", "data": res.data[0]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- PUNTO DE VENTA ---
@app.post("/ventas", tags=["Punto de Venta"])
async def procesar_venta(payload: dict = Body(...)):
    try:
        items = payload.get("items", [])
        store_id = int(payload.get("store_id", 1)) # Usamos store_id directo

        if not items:
            raise HTTPException(status_code=400, detail="Carrito vacío")

        # --- 1. VALIDACIÓN PREVIA DE STOCK ---
        for item in items:
            barcode = int(item["barcode"])
            qty_solicitada = int(item["quantity"])
            
            # CORRECCIÓN: Se cambió 'store' por 'store_id'
            stock_res = supabase.table("medicaments_stock") \
                .select("stock, medicaments(name)") \
                .eq("barcode", barcode) \
                .eq("store_id", store_id).execute()

            if not stock_res.data:
                raise HTTPException(
                    status_code=404, 
                    detail=f"Producto {barcode} no registrado en sucursal {store_id}"
                )
            
            stock_actual = stock_res.data[0]["stock"]
            nombre_prod = stock_res.data[0].get("medicaments", {}).get("name", "Producto")

            if stock_actual < qty_solicitada:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Stock insuficiente para {nombre_prod}. Disponible: {stock_actual}"
                )

        # --- 2. INSERTAR TICKET ---
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
            "store_id": store_id,
            "folio": 0
        }
        
        ticket_res = supabase.table("tickets").insert(ticket_data).execute()
        nuevo_ticket_id = ticket_res.data[0]["id"]

        # Generar Folio
        fecha_str = datetime.now().strftime("%Y%m%d")   
        folio_numerico = int(f"{fecha_str}{store_id}{nuevo_ticket_id}")
        supabase.table("tickets").update({"folio": folio_numerico}).eq("id", nuevo_ticket_id).execute()

        # --- 3. ACTUALIZACIÓN DE STOCK ---
        for item in items:
            b_code = int(item["barcode"])
            qty = int(item["quantity"])

            supabase.table("ticket_details").insert({
                "ticket_id": nuevo_ticket_id,
                "barcode": b_code,
                "quantity": qty,
                "price_at_sale": float(item["price"])
            }).execute()

            # CORRECCIÓN: Se cambió 'store' por 'store_id'
            curr = supabase.table("medicaments_stock") \
                .select("stock") \
                .eq("barcode", b_code) \
                .eq("store_id", store_id).execute()
            
            nuevo_stock = curr.data[0]["stock"] - qty

            supabase.table("medicaments_stock") \
                .update({"stock": nuevo_stock}) \
                .eq("barcode", b_code) \
                .eq("store_id", store_id).execute()

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
    
@app.post("/loyalty/vincular-ticket", tags=["Tarjetas de Lealtad"])
async def vincular_ticket_a_tarjeta(payload: dict = Body(...)):
    try:
        folio_buscado = payload.get("folio")
        store_id = payload.get("store_id")
        num_tarjeta = payload.get("card")

        if not all([folio_buscado, store_id, num_tarjeta]):
            raise HTTPException(status_code=400, detail="Faltan datos: folio, store_id o card")

        # 1. Verificar que la tarjeta de lealtad existe
        card_res = supabase.table("loyalty_cards") \
            .select("id") \
            .eq("card", num_tarjeta) \
            .eq("active", True) \
            .execute()

        if not card_res.data:
            raise HTTPException(status_code=404, detail="La tarjeta no existe o está inactiva")
        
        id_interno_tarjeta = card_res.data[0]["id"]
        

        # Convertimos a string para asegurar que Supabase haga match exacto
        folio_str = str(payload.get("folio"))
        store_id_int = int(payload.get("store_id"))

        ticket_res = supabase.table("tickets") \
            .select("id", "card_id", "card") \
            .eq("folio", folio_str) \
            .eq("store_id", store_id_int) \
            .execute()

        if not ticket_res.data:
            raise HTTPException(status_code=404, detail="Ticket no encontrado en esta sucursal")

        ticket_actual = ticket_res.data[0]

        # 3. Validar si el ticket ya tiene una tarjeta asignada
        # Verificamos card_id porque es la FK principal
        if ticket_actual.get("card_id") is not None:
            raise HTTPException(
                status_code=400, 
                detail=f"El ticket ya está vinculado a la tarjeta terminación {ticket_actual.get('card')}"
            )

        # 4. Realizar la vinculación (Update)
        update_res = supabase.table("tickets") \
            .update({
                "card_id": id_interno_tarjeta,
                "card": num_tarjeta
            }) \
            .eq("id", ticket_actual["id"]) \
            .execute()

        return {
            "status": "success",
            "message": "Ticket vinculado exitosamente",
            "ticket_id": ticket_actual["id"],
            "folio": folio_buscado
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error en vinculación: {str(e)}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    
    
# --- ANALÍTICAS ---

@app.post("/analytics/log", tags=["Analiticas"])
async def log_bot_activity(payload: dict = Body(...)):
    """
    Registra la actividad del bot: intención, duración de la acción, 
    sucursal y sesión del usuario.
    """
    try:
        log_data = {
            "session_id": payload.get("session_id"),
            "user_id": payload.get("user_id"), # ID del usuario/doctor
            "intent": payload.get("intent"),
            "action": payload.get("action"),
            "store_id": payload.get("store_id"),
            "duration": float(payload.get("duration", 0)),
            "created_at": datetime.now().isoformat()
        }
        
        res = supabase.table("bot_analytics").insert(log_data).execute()
        return {"status": "success", "message": "Actividad registrada"}
    except Exception as e:
        print(f"Error en logs: {str(e)}")
        # No lanzamos HTTPException para no interrumpir el flujo del bot si falla el log
        return {"status": "error", "detail": str(e)}
    
@app.get("/analytics/summary", tags=["Analiticas"])
async def get_analytics_summary():
    """
    Obtiene métricas clave: Usuarios únicos, tiempo promedio y uso total.
    """
    try:
        # 1. Total de interacciones
        total_res = supabase.table("bot_analytics").select("id", count="exact").execute()
        total_count = total_res.count

        # 2. Usuarios únicos
        unique_users_res = supabase.table("bot_analytics").select("user_id").execute()
        unique_count = len(set([u["user_id"] for u in unique_users_res.data if u["user_id"]]))

        # 3. Tiempo promedio de vinculación de ticket
        avg_res = supabase.table("bot_analytics") \
            .select("duration") \
            .eq("intent", "vincular_ticket") \
            .execute()
        
        avg_time = 0
        if avg_res.data:
            durations = [d["duration"] for d in avg_res.data]
            avg_time = sum(durations) / len(durations)

        return {
            "total_interactions": total_count,
            "unique_users": unique_count,
            "avg_ticket_binding_time": round(avg_time, 2),
            "status": "active"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/analytics/hourly-usage", tags=["Analiticas"])
async def get_hourly_usage():
    """
    Retorna la distribución de uso por hora para el Heatmap del dashboard.
    """
    try:
        res = supabase.table("bot_analytics").select("created_at").execute()
        
        # Inicializar diccionario de 24 horas
        hourly_data = {i: 0 for i in range(24)}
        
        for row in res.data:
            # Extraer la hora del string ISO
            dt = datetime.fromisoformat(row["created_at"])
            hourly_data[dt.hour] += 1
            
        # Formatear para gráficos de React (ej. Recharts)
        return [{"hora": f"{h:02d}:00", "cantidad": c} for h, c in hourly_data.items()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))