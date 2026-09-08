from google.adk.agents.llm_agent import Agent
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, sum
 
spark = SparkSession.builder\
.master("local[*]")\
.appName("ADK_PySpark_Demo")\
.getOrCreate()
 
data = [
    ('North', 'Laptop', 1200),
    ('North', 'Phone', 1500),
    ('North', 'Tablet', 800),
    ('North', 'Monitor', 600),
    ('North', 'Printer', 400),
    ('North', 'Laptop', 1800),
    ('North', 'Phone', 1300),
    ('North', 'Tablet', 950),
    ('North', 'Monitor', 750),
    ('North', 'Printer', 500),

    ('South', 'Laptop', 1800),
    ('South', 'Phone', 800),
    ('South', 'Tablet', 700),
    ('South', 'Monitor', 900),
    ('South', 'Printer', 550),
    ('South', 'Laptop', 2100),
    ('South', 'Phone', 1250),
    ('South', 'Tablet', 850),
    ('South', 'Monitor', 950),
    ('South', 'Printer', 450),

    ('East', 'Laptop', 900),
    ('East', 'Phone', 1100),
    ('East', 'Tablet', 650),
    ('East', 'Monitor', 700),
    ('East', 'Printer', 350),
    ('East', 'Laptop', 1450),
    ('East', 'Phone', 1350),
    ('East', 'Tablet', 780),
    ('East', 'Monitor', 820),
    ('East', 'Printer', 420),

    ('West', 'Laptop', 1700),
    ('West', 'Phone', 1300),
    ('West', 'Tablet', 920),
    ('West', 'Monitor', 850),
    ('West', 'Printer', 480),
    ('West', 'Laptop', 1950),
    ('West', 'Phone', 1400),
    ('West', 'Tablet', 980),
    ('West', 'Monitor', 890),
    ('West', 'Printer', 520),

    ('Central', 'Laptop', 1600),
    ('Central', 'Phone', 1450),
    ('Central', 'Tablet', 840),
    ('Central', 'Monitor', 780),
    ('Central', 'Printer', 460),
    ('Central', 'Laptop', 2200),
    ('Central', 'Phone', 1550),
    ('Central', 'Tablet', 900),
    ('Central', 'Monitor', 920),
    ('Central', 'Printer', 610)
]
 
df = spark.createDataFrame(
    data,
    ['Region','Product','Sales']
)
 
def region_sales_summary():
  result = (
    df.filter(col("Product")=='Laptop')
    .groupby("Region")
    .agg(sum('Sales').alias('TotalSales'))
    .orderBy(col('TotalSales').desc())
    .toPandas()
  )
  return result.to_dict(orient="records")
 
root_agent = Agent(
    model='gemini-3.5-flash-lite',
    name='sales_agent',
    description='Answer sales questions using available tools',
    instruction='Answer sales questions using available tools',
    tools = [region_sales_summary]
)
 