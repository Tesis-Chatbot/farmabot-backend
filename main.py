from fastapi import FastAPI, HTTPException, Body
from database import supabase
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Chatbot Farmacia")

# Configuración de CORS
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Conexión exitosa"}

@app.get("/medicamentos")
def get_medicamentos():
    # Consultamos medicamentos y pedimos que traiga la relación con stock
    response = supabase.table("medicaments").select("*, medicaments_stock(stock)").execute()
    
    productos_con_stock = []
    
    # Verificamos que response.data no sea None
    data = response.data if response.data else []
    
    for item in data:
        # Manejamos si medicaments_stock es None con 'or []'
        stock_entries = item.get('medicaments_stock') or []
        
        # Sumamos el stock de todas las sucursales para ese medicamento
        total_stock = sum(s.get('stock', 0) for s in stock_entries)
        
        # Limpiamos el objeto para el front
        nuevo_item = item.copy()
        nuevo_item['stock'] = total_stock
        
        # Borramos la relación original para enviar un JSON limpio
        if 'medicaments_stock' in nuevo_item:
            del nuevo_item['medicaments_stock']
            
        productos_con_stock.append(nuevo_item)
        
    return productos_con_stock

@app.post("/ventas")
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