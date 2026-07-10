from openai import OpenAI
import re
client = OpenAI()


class Agents:
    def __init__(self, image,task_description, child_context = ""):
        if isinstance(image,(list, tuple)):

            self.encoded_images = [item for item in image if item]
        else:
            self.encoded_images = [image] if image else []
        self.encoded_image = self.encoded_images[0] if self.encoded_images else ""
        self.task_description = task_description

        self.child_context = child_context or ""

        self.visual_task_description = self.visual_only_task_description()

    def image_prompt_prefix(self):
        if len(self.encoded_images) <= 1:
            return ""
        return (
            "You are given multiple RGB views captured by the same robot during a "
            "fixed-position 360 degree scan. Treat them as complementary views of "
            "one scene. Objects can appear in only one view, so inspect all views "
            "before answering.\n"
        )

    def image_content(self):
        content = []

        multiple = len(self.encoded_images) > 1
        for index, encoded_image in enumerate(self.encoded_images):
            if multiple:
                content.append({"type": "text","text": f"View {index}:"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded_image}",
                    },
                }
            )
        return content

    def non_visual_context_prompt(self):
        if not self.child_context.strip():
            return ""
        return (
            "\nAdditional non-visual world knowledge:\n"
            + self.child_context.strip()
            + "\n\nThe child may be used in the plan even if it is not visible in the image.\n"
            "If the task asks to bring an object to the child, include NAVIGATE to the child "
            "before DROP.\n"
            "After navigating to a destination while carrying an object, DROP <object> means "
            "dropping it at the current destination.\n"
        )

    def visual_only_task_description(self):
        if not self.mentions_child(self.task_description):
            return self.task_description

        child_pattern = r"(?:the\s+)?(?:child|kid)"
        parts = re.split(r"\s*,\s*|\s+\band\b\s+", self.task_description,flags=re.IGNORECASE)
        kept_parts = []
        for part in parts:
            cleaned = part.strip()
            if not cleaned:
                continue
            if self.mentions_child(cleaned):
                cleaned = re.sub(
                    rf"\s+(?:to|towards|near|at|for)\s+{child_pattern}\b",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                ).strip()
                if re.fullmatch(r"(?:go|navigate|move|walk|give\s+it|bring\s+it)",cleaned, flags=re.IGNORECASE):
                    continue
            if cleaned and not self.mentions_child(cleaned):
                kept_parts.append(cleaned)

        visual_task =", ".join(kept_parts).strip()
        if visual_task:

            return visual_task

        visual_task = re.sub(
            rf"\s+(?:to|towards|near|at|for)\s+{child_pattern}\b",
            "",
            self.task_description,
            flags=re.IGNORECASE,
        ).strip()
        return visual_task or "identify the visible objects needed for the task"

    def mentions_child(self,text):
        normalized= str(text).lower()
        return re.search(r"\b(?:the\s+)?(?:child|kid)\b",normalized) is not None
    
    def single_agent(self):
            agent = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                "role": "user",
                "content": [
                    {
                    "type": "text",
                       "text": self.image_prompt_prefix() +"You are a mobile robot with a base allow you to move around the environment.\n\
                            You have a robotic arm with a gripper that allows you to pick up and place one object at time.\n\
                            You are also very capable in describing a scene provided an image in input.\n\
                            From the image, you need to produce in the output a set of relations in the in the form of a triple (subject, relation, object). \n\
                            Write just the triples that are essential to solve the following task: " + self.task_description + "\n\
                            Use specific relation to describe the position of the objects in the scene. Do not use 'next to' but you must \
                            use 'right to', 'left to', 'behind to', 'beside to', 'on'\n\
                            For example, if in a scene there is a door, a table in front of the door and a book on the table \
                            with a pen right to it, your answer should be: \
                            1) (table, in front of, door) \n\
                            2) (book, on, table) \n\
                            3) (pen, on, table) \n\
                            4) (pen, right to, book). \n\
                            For the same task given in input you, should plan a sequence of actions to solve the task.\n\
                            Use univocal name given in the relations of the environment to specify the object.\n\
                            Work as a Markovian agent, so you can only see the last action and the current state of the environment.\n\
                            After each step, update the state of the environment to elaborate the next step execuable in the updated enviroment.\
                            You must use only the following actions for the plan and nothing else: \n\
                            NAVIGATE : for the movement in the scene towards a point far from you, for example 'NAVIGATE to the table'\n\
                            GRAB : for the action of picking up an object and specifying which object to grab, for example 'GRAB bottle'\n\
                            DROP : for the action of placing an object, either at the current destination after navigation, for example 'DROP book', or specifying where with respect to another object, for example 'DROP bottle left to mug' or 'DROP mug right to bottle' or 'DROP pen into bag'\n\
                            PULL : for the action of pulling an object with the gripper,\n\
                            PUSH: for the action of pushing an object on the ground with the base to free its trajectory if necessary.\n\
                            " + self.non_visual_context_prompt() + "\
                            Write only the actions for the plan and nothing else\n\
                            The output should be in this format:\n\
                            '***RELATIONS***\n\
                            set of relations obtained\n\
                            ***PLAN***\n\
                            set of steps to achieve the task\n'"},
                    *self.image_content(),
                ],
                }
            ],
            max_tokens=500,
            temperature=0,
            )
            response=(agent.choices[0].message.content)
            return response

    def multi_agent_vision_planning(self):
    
        def enviroment_agent():
            agent = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                "role": "user",
                "content": [
                    {
                    "type": "text",
                        "text": self.image_prompt_prefix()+"You are an assistent which is able to accurately describe the content of the provided image or images. \n\
                        In particular, you are able to capture the main objects present \
                        across all views and to provide all the relations that exist between them. \n\
                        These relations are described in the form of a triple (subject, relation, object) \
                        and when you answer you are only expected to answer with triples and nothing else. \n\
                        If multiple views are provided, write a unified set of triples for the whole scene, not one answer per view. \
                        Write just the triples that are essential to solve the following visible part of the task: " + self.visual_task_description + "\n\
                        Use specific relation to describe the position of the objects in the scene. Do not use 'next to' but you must \
                        use 'right to', 'left to', 'behind to', 'beside to', 'on'\n\
                        For example, if in a scene there is a door, a table in front of the door and a book on the table \
                        with a pen right to it, your answer should be: \
                        1) (table, in front of, door) \n\
                        2) (book, on, table) \n\
                        3) (pen, on, table) \n\
                        4) (pen, right to, book."},
                    *self.image_content(),
                ],
                }
            ],
            max_tokens=700,
            temperature=0,
            )

            response = (agent.choices[0].message.content)
            return response


        def description_agent():
            agent = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                "role": "user",
                "content": [
                    {
                    "type": "text",
                        "text": self.image_prompt_prefix() + "You are an assistent which is able to accurately describe the content of the provided image or images. \n\
                            In particular, you are able to accurately describe the content of the image to make one understand \
                            all the details of the scene without seeing it. \n\
                            Use the name in the following relations : " + enviroment_info +" and nothing else without adding adjectives for the objects.\n\
                            You should describe how the scene is made with high level description and precise instruction to solve \
                            the following visible part of the task : " + self.visual_task_description+". Try to minimize the steps required and find the best plan to solve\n\
                            If the task contains ambiguity in the solution of the task, for example many objects of the same type,\
                            specify the position of the object in the image or in relation to other objects.\n"},
                    *self.image_content(),
                ],
                }
            ],
            max_tokens=900,
            temperature=0,
            )

            response = (agent.choices[0].message.content)
            return response

        
        enviroment_info = enviroment_agent()
        description_agent_info = description_agent()

        agent = client.chat.completions.create(
        model="gpt-4o",
        messages=[
        {"role": "system","content":
            "You are a mobile robot with a base allow you to move around the environment.\n\
            You have a robotic arm with a gripper that allows you to pick up and place one object at time.\n\
            You know the detailed description of the scene and preliminar instructions that can help you in the definition of the plan \
            with the following information : " + description_agent_info +"\n\
            Remember that you have to use this information just as an help.\n\
            For the task given in input you should plan a sequence of actions to solve the task.\n\
            Use univocal name given in the relations of the environment to specify the object.\n\
            Work as a Markovian agent, so you can only see the last action and the current state of the environment.\n\
            After each step, update the state of the environment to elaborate the next step execuable in the updated enviroment."
            + self.non_visual_context_prompt()
            },
        {"role": "user", "content": 
            "The task is " + self.task_description+"\n\
                You must use only the following actions for the plan and nothing else: \n\
                            NAVIGATE : for the movement in the scene towards a point far from you, for example 'NAVIGATE to the table'\n\
                            GRAB : for the action of picking up an object and specifying which object to grab, for example 'GRAB bottle'\n\
                            DROP : for the action of placing an object, either at the current destination after navigation, for example 'DROP book', or specifying where with respect to another object, for example 'DROP bottle left to mug' or 'DROP mug right to bottle' or 'DROP pen into bag'\n\
                            PULL : for the action of pulling an object with the gripper,\n\
                            PUSH: for the action of pushing an object on the ground with the base to free its trajectory if necessary.\n\
                            Write only the actions for the plan and nothing else"},
        ],
        temperature=0,
        max_tokens=600,
        )
        return enviroment_info,description_agent_info, agent.choices[0].message.content

    def plan_from_scene_description(self, scene_description):
        agent =client.chat.completions.create(
        model="gpt-4o",
        messages=[
        {"role": "system","content":
            "You are a mobile robot with a base that allows you to move around the environment.\n"
            "You have a robotic arm with a gripper that allows you to pick up and place one object at a time.\n"
            "You are given a deduplicated global scene description from a multi-view RGB-D scan.\n"
            "Use object ids and aliases from the global scene when selecting visual targets.\n"
            + self.non_visual_context_prompt()
            },
        {"role": "user", "content":
            "The task is "+ self.task_description + "\n\n"
            "Global scene:\n" + str(scene_description) + "\n\n"
            "You must use only the following actions for the plan and nothing else:\n"
            "NAVIGATE : for movement in the scene towards a point far from you, for example 'NAVIGATE to the table'\n"
            "GRAB : for picking up an object and specifying which object to grab, for example 'GRAB bottle'\n"
            "DROP : for placing an object, either at the current destination after navigation, for example 'DROP book', "
            "or specifying where with respect to another object, for example 'DROP bottle left to mug'\n"
            "PULL : for pulling an object with the gripper\n"
            "PUSH : for pushing an object on the ground with the base\n"
            "Write only the actions for the plan and nothing else"},
        ],
        temperature=0,
        max_tokens=600,
        )
        return agent.choices[0].message.content
