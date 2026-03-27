FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src

COPY ManagerApp/ManagerApp.csproj ManagerApp/
RUN dotnet restore ManagerApp/ManagerApp.csproj

COPY . .
RUN dotnet publish ManagerApp/ManagerApp.csproj -c Release -o /app/publish /p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime
WORKDIR /app
COPY --from=build /app/publish .

ENV ASPNETCORE_URLS=http://+:5175
EXPOSE 5175

ENTRYPOINT ["dotnet", "ManagerApp.dll"]
