workspace "Schwinn Workout Application" "Architecture model for the Schwinn workout tracking application." {

    model {
        user = person "User" "Views workout history, charts, ride metrics, and manages ride data."

        schwinn = softwareSystem "Schwinn Workout Application" "Tracks Schwinn workout history, ride statistics, charts, and optional ride exports." {
            web = container "Web UI" "Browser-based interface for viewing workouts, charts, and statistics." "HTML/CSS/JavaScript"

            api = container "Python Web Application" "Serves pages and APIs, parses workout data, calculates metrics, generates charts, and exposes metrics." "FastAPI / Python" {
                main = component "Application Entry Point" "Creates the FastAPI app, registers routers, middleware, templates, static files, and metrics." "app/main.py or app/app.py"

                config = component "Configuration" "Loads environment variables, file paths, and runtime settings." "core/config.py"
                logging = component "Logging" "Configures structured application logging and rotating log files." "core/logging.py"
                metrics = component "Metrics" "Captures endpoint metrics and exposes Prometheus metrics." "core/metrics.py"

                authRoutes = component "Auth Routes" "Handles login, logout, sessions, and auth-related requests." "auth/routes.py"
                authService = component "Auth Service" "Validates users and manages authentication logic." "auth/service.py"
                authDb = component "Auth Data Access" "Reads and writes user/session data." "auth/db.py"
                authModels = component "Auth Models" "Defines auth-related data structures." "auth/models.py"

                workoutRoutes = component "Workout Routes" "Provides workout pages and API endpoints." "workouts/routes.py"
                workoutParser = component "Workout Parser" "Parses Schwinn workout history CSV data." "workouts/parsing.py"
                workoutHistory = component "Workout History Service" "Loads, filters, aggregates, and summarizes workout history." "workouts/history.py"
                chartGenerator = component "Chart Generator" "Builds charts and visual summaries from workout metrics." "workouts/charts.py"
            }

            data = container "Workout Data Store" "Stores imported workout history and calculated ride metadata." "CSV / SQLite"
            logs = container "Application Logs" "Stores rotating application logs for troubleshooting and auditability." "Log files"
        }

        prometheus = softwareSystem "Prometheus" "Scrapes application metrics."
        strava = softwareSystem "Strava API" "Optional external ride upload destination."
        mapmyride = softwareSystem "MapMyRide" "Potential future external ride upload destination."

        user -> web "Uses" "HTTP/HTTPS"
        web -> api "Calls" "HTTP/JSON"

        api -> data "Reads and writes workout history"
        api -> logs "Writes logs"
        prometheus -> api "Scrapes /metrics" "HTTP"
        api -> strava "Uploads rides" "HTTPS/API"
        api -> mapmyride "Potential future integration" "HTTPS/API"

        main -> config "Loads settings from"
        main -> logging "Initializes"
        main -> metrics "Initializes"
        main -> authRoutes "Registers router"
        main -> workoutRoutes "Registers router"

        authRoutes -> authService "Delegates authentication logic to"
        authService -> authDb "Reads/writes auth data through"
        authService -> authModels "Uses"

        workoutRoutes -> workoutHistory "Requests workout summaries from"
        workoutRoutes -> workoutParser "Imports workout files through"
        workoutRoutes -> chartGenerator "Requests chart generation from"

        workoutParser -> data "Reads imported workout data from"
        workoutHistory -> data "Reads/writes workout records"
        chartGenerator -> workoutHistory "Uses aggregated workout data"

        logging -> logs "Writes"
        metrics -> prometheus "Exposes metrics to"
    }

    views {
        systemContext schwinn "c1-system-context" {
            include *
            autolayout lr
        }

        container schwinn "c2-container" {
            include *
            autolayout lr
        }

        component api "c3-component" {
            include *
            autolayout tb
        }

        styles {
            element "Person" {
                shape person
                background #08427b
                color #ffffff
            }

            element "Software System" {
                background #1168bd
                color #ffffff
            }

            element "Container" {
                background #438dd5
                color #ffffff
            }

            element "Component" {
                background #85bbf0
                color #000000
            }

            element "Database" {
                shape cylinder
            }
        }

        theme default
    }
}
