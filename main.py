import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from database import supabase

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
        "name": "Sistema",
        "description": "Validación de estado del servidor.",
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

@app.post("/ventas", tags=["Punto de Venta"])
async def procesar_venta(payload: dict = Body(...)):
    try:
        items = payload.get("items", [])
        if not items:
            raise HTTPException(status_code=400, detail="Carrito vacío")

        # 1. Buscar tarjeta de lealtad para obtener el ID numérico
        card_id = None
        if payload.get("card_number"):
            # Buscamos en loyalty_cards usando el número de 14 dígitos
            card_res = supabase.table("loyalty_cards").select("id").eq("card", payload["card_number"]).execute()
            if card_res.data:
                card_id = card_res.data[0]["id"]

        # 2. Insertar Ticket (Cabecera)
        # Nota: 'folio' e 'id' son automáticos si están configurados como serial/identity
        ticket_data = {
            "total": float(payload["total"]),
            "card_id": card_id, # El ID (FK)
            "card": int(payload["card_number"]) if payload.get("card_number") else None, # El número de 14 dígitos como int
            "payment_method": "Efectivo" # Esto ya debería entrar como string
        }
        
        ticket_res = supabase.table("tickets").insert(ticket_data).execute()
        
        if not ticket_res.data:
            raise Exception("No se pudo insertar el ticket")
            
        nuevo_ticket_id = ticket_res.data[0]["id"]

        # 3. Detalles y Actualización de Stock
        for item in items:
            # A. Insertar detalle
            detalle = {
                "ticket_id": nuevo_ticket_id,
                "barcode": int(item["barcode"]), # Convertimos a int para tu FK
                "quantity": int(item["quantity"]),
                "price_at_sale": float(item["price"])
            }
            supabase.table("ticket_details").insert(detalle).execute()

            # B. Actualizar Stock
            # Buscamos el stock actual en la sucursal 1 (simulada)
            stock_res = supabase.table("medicaments_stock") \
                .select("stock") \
                .eq("barcode", item["barcode"]) \
                .eq("store", 1).execute()

            if stock_res.data:
                nuevo_stock = stock_res.data[0]["stock"] - item["quantity"]
                supabase.table("medicaments_stock") \
                    .update({"stock": nuevo_stock}) \
                    .eq("barcode", item["barcode"]) \
                    .eq("store", 1).execute()

        return {"status": "success", "ticket_id": nuevo_ticket_id}

    except Exception as e:
        print(f"Error detallado: {str(e)}")
        # Devolvemos el error para que lo veas en el alert de React
        raise HTTPException(status_code=500, detail=str(e))