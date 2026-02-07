# farmabot-backend
API REST centralizada para el ecosistema Farmabot. Gestiona la lógica de negocio, la persistencia de datos en Supabase y la sincronización de inventarios en tiempo real.

## Funcionalidades
- Gestión de Inventarios: Control de stock por sucursal y búsqueda avanzada de medicamentos.
- Procesamiento Transaccional: Endpoint /ventas que garantiza la integridad de los datos (actualización de stock + creación de ticket + vinculación de cliente).
- Sistema de Lealtad: Validación y consulta de beneficios mediante tarjetas de 14 dígitos.
- Arquitectura Escalable: Basado en FastAPI para alto rendimiento y validación de datos mediante Pydantic.

## Stack Tecnológico
- Lenguaje: Python 3.10+
- Framework: FastAPI
- Base de Datos: PostgreSQL (vía Supabase)
- ORM / Query Builder: Supabase-py
- Servidor ASGI: Uvicorn

 ## Endpoints Principales
| Método | Endpoint | Descripción |
| :--- | :--- | :--- |
| `GET` | `/medicamentos` | Lista y filtra el inventario disponible. |
| `POST` | `/ventas` | Registra una nueva venta y descuenta stock. |
| `GET` | `/loyalty/{card}` | Verifica el estatus de una tarjeta. |

## Autor
Leonardo Pantoja Canchola - Ingeniería de Software - Matrícula: 214960
